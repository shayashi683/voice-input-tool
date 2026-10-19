#import "VITUpdateController.h"
#import <CommonCrypto/CommonDigest.h>
#include <sys/stat.h>

static NSString *const VITUpdateHost = @"voice-input-tool.pages.dev";
static NSUInteger const VITPackageLimit = 25 * 1024 * 1024;

static NSError *UpdateError(NSString *message) {
    return [NSError errorWithDomain:@"VoiceInputTool.Update" code:1
                          userInfo:@{NSLocalizedDescriptionKey: message}];
}

static BOOL Matches(id value, NSString *pattern) {
    if (![value isKindOfClass:NSString.class]) return NO;
    NSRegularExpression *regex = [NSRegularExpression regularExpressionWithPattern:pattern options:0 error:nil];
    NSTextCheckingResult *match = [regex firstMatchInString:value options:0 range:NSMakeRange(0, [value length])];
    return match && NSEqualRanges(match.range, NSMakeRange(0, [value length]));
}

BOOL VITUpdateVersionIsValid(id version) {
    return Matches(version, @"[0-9]{1,6}\\.[0-9]{1,6}\\.[0-9]{1,6}");
}

NSComparisonResult VITCompareUpdateVersions(NSString *left, NSString *right) {
    NSArray *a = [left componentsSeparatedByString:@"."];
    NSArray *b = [right componentsSeparatedByString:@"."];
    for (NSUInteger index = 0; index < 3; index++) {
        NSInteger x = [a[index] integerValue], y = [b[index] integerValue];
        if (x < y) return NSOrderedAscending;
        if (x > y) return NSOrderedDescending;
    }
    return NSOrderedSame;
}

static BOOL TrustedURL(NSURL *url) {
    return [url.scheme isEqualToString:@"https"] && [url.host isEqualToString:VITUpdateHost] &&
        !url.user && !url.password && !url.port && !url.query && !url.fragment;
}

NSDictionary *VITValidateUpdateManifest(NSData *data, NSError **error) {
    id value = data.length > 0 && data.length <= 65536 ? [NSJSONSerialization JSONObjectWithData:data options:0 error:nil] : nil;
    if (![value isKindOfClass:NSDictionary.class]) {
        if (error) *error = UpdateError(@"更新情報を読み込めませんでした。");
        return nil;
    }
    NSDictionary *release = value;
    NSString *version = release[@"version"];
    NSString *expected = VITUpdateVersionIsValid(version) ? [NSString stringWithFormat:
        @"https://voice-input-tool.pages.dev/downloads/VoiceInputTool-%@-mac-arm64.pkg", version] : nil;
    NSNumber *size = release[@"size"];
    NSNumber *schema = release[@"schema_version"];
    BOOL integerSize = [size isKindOfClass:NSNumber.class] &&
        CFGetTypeID((__bridge CFTypeRef)size) != CFBooleanGetTypeID() &&
        size.doubleValue == (double)size.unsignedLongLongValue;
    if (![schema isKindOfClass:NSNumber.class] || CFGetTypeID((__bridge CFTypeRef)schema) == CFBooleanGetTypeID() ||
        schema.doubleValue != 1 || !expected || ![release[@"url"] isEqual:expected] ||
        !Matches(release[@"sha256"], @"[0-9a-f]{64}") || !integerSize ||
        size.unsignedLongLongValue < 4 || size.unsignedLongLongValue > VITPackageLimit ||
        ![release[@"architecture"] isEqual:@"arm64"] ||
        !VITUpdateVersionIsValid(release[@"minimum_macos"]) ||
        ![release[@"notes"] isKindOfClass:NSString.class] || [release[@"notes"] length] > 4000) {
        if (error) *error = UpdateError(@"更新情報の形式または配布先が正しくありません。");
        return nil;
    }
    return release;
}

BOOL VITVerifyUpdatePackage(NSData *data, NSDictionary *release) {
    if (data.length != [release[@"size"] unsignedLongLongValue] || data.length < 4 ||
        data.length > VITPackageLimit || memcmp(data.bytes, "xar!", 4) != 0) return NO;
    unsigned char digest[CC_SHA256_DIGEST_LENGTH];
    CC_SHA256(data.bytes, (CC_LONG)data.length, digest);
    NSMutableString *hex = [NSMutableString string];
    for (NSUInteger i = 0; i < CC_SHA256_DIGEST_LENGTH; i++) [hex appendFormat:@"%02x", digest[i]];
    return [hex isEqualToString:release[@"sha256"]];
}

// Bounded HTTPS transfer. No cookies, credentials, redirects or background requests.
@interface VITUpdateTransfer : NSObject <NSURLSessionDataDelegate>
@property(nonatomic, strong) NSURLSession *session;
@property(nonatomic, strong) NSURLSessionDataTask *task;
@property(nonatomic, strong) NSMutableData *data;
@property(nonatomic, assign) NSUInteger limit;
@property(nonatomic, copy) void (^completion)(NSData *, NSError *);
- (void)startURL:(NSURL *)url limit:(NSUInteger)limit completion:(void (^)(NSData *, NSError *))completion;
- (void)cancel;
@end

@implementation VITUpdateTransfer
- (void)startURL:(NSURL *)url limit:(NSUInteger)limit completion:(void (^)(NSData *, NSError *))completion {
    self.completion = completion;
    self.limit = limit;
    self.data = [NSMutableData data];
    if (!TrustedURL(url)) { [self finish:UpdateError(@"配布先URLを確認できません。")]; return; }
    NSURLSessionConfiguration *config = NSURLSessionConfiguration.ephemeralSessionConfiguration;
    config.timeoutIntervalForRequest = 30;
    config.timeoutIntervalForResource = 120;
    config.requestCachePolicy = NSURLRequestReloadIgnoringLocalCacheData;
    config.URLCache = nil;
    config.HTTPCookieStorage = nil;
    config.URLCredentialStorage = nil;
    self.session = [NSURLSession sessionWithConfiguration:config delegate:self delegateQueue:NSOperationQueue.mainQueue];
    self.task = [self.session dataTaskWithURL:url];
    [self.task resume];
}
- (void)finish:(NSError *)error {
    void (^completion)(NSData *, NSError *) = self.completion;
    self.completion = nil;
    [self.session invalidateAndCancel];
    self.session = nil;
    self.task = nil;
    if (completion) completion(error ? nil : self.data, error);
    self.data = nil;
}
- (void)cancel { [self finish:[NSError errorWithDomain:NSURLErrorDomain code:NSURLErrorCancelled userInfo:nil]]; }
- (void)URLSession:(NSURLSession *)session task:(NSURLSessionTask *)task
 willPerformHTTPRedirection:(NSHTTPURLResponse *)response newRequest:(NSURLRequest *)request
 completionHandler:(void (^)(NSURLRequest *))completionHandler {
    completionHandler(nil);
    [self finish:UpdateError(@"配布先のリダイレクトを拒否しました。")];
}
- (void)URLSession:(NSURLSession *)session dataTask:(NSURLSessionDataTask *)task
 didReceiveResponse:(NSURLResponse *)response completionHandler:(void (^)(NSURLSessionResponseDisposition))completionHandler {
    BOOL valid = [response isKindOfClass:NSHTTPURLResponse.class] &&
        [(NSHTTPURLResponse *)response statusCode] == 200 && TrustedURL(response.URL) &&
        response.expectedContentLength <= (int64_t)self.limit;
    completionHandler(valid ? NSURLSessionResponseAllow : NSURLSessionResponseCancel);
    if (!valid) [self finish:UpdateError(@"更新サーバーの応答が正しくありません。時間をおいて再試行してください。")];
}
- (void)URLSession:(NSURLSession *)session dataTask:(NSURLSessionDataTask *)task didReceiveData:(NSData *)data {
    if (self.data.length + data.length > self.limit) {
        [self finish:UpdateError(@"更新ファイルがサイズ上限を超えています。")];
    } else { [self.data appendData:data]; }
}
- (void)URLSession:(NSURLSession *)session task:(NSURLSessionTask *)task didCompleteWithError:(NSError *)error {
    [self finish:error];
}
@end

@interface VITUpdateController ()
@property(nonatomic, copy) NSString *currentVersion;
@property(nonatomic, copy) BOOL (^canInstall)(void);
@property(nonatomic, copy) BOOL (^install)(NSURL *);
@property(nonatomic, strong) NSDictionary *availableRelease;
@property(nonatomic, strong) VITUpdateTransfer *transfer;
@property(nonatomic, strong) NSTextField *statusLabel;
@property(nonatomic, strong) NSButton *actionButton;
@property(nonatomic, strong) NSButton *closeButton;
@property(nonatomic, strong) NSProgressIndicator *progress;
@property(nonatomic, copy) NSString *downloadDirectory;
@property(nonatomic, strong) NSURL *packageURL;
@property(nonatomic, assign) NSUInteger serial;
@property(nonatomic, assign) BOOL busy;
@property(nonatomic, assign) BOOL handingOff;
@end

@implementation VITUpdateController
- (instancetype)initWithCurrentVersion:(NSString *)version canInstallHandler:(BOOL (^)(void))canInstall
                       installHandler:(BOOL (^)(NSURL *))install {
    NSWindow *window = [[NSWindow alloc] initWithContentRect:NSMakeRect(0, 0, 520, 340)
        styleMask:NSWindowStyleMaskTitled | NSWindowStyleMaskClosable backing:NSBackingStoreBuffered defer:NO];
    self = [super initWithWindow:window];
    if (!self) return nil;
    self.currentVersion = version ?: @"";
    self.canInstall = canInstall;
    self.install = install;
    window.title = @"Voice Input Tool — ソフトウェア更新";
    window.delegate = self;
    [window center];
    NSTextField *title = [NSTextField labelWithString:[NSString stringWithFormat:@"現在のバージョン：%@", self.currentVersion]];
    title.font = [NSFont boldSystemFontOfSize:17];
    title.frame = NSMakeRect(24, 288, 472, 26);
    [window.contentView addSubview:title];
    self.statusLabel = [NSTextField wrappingLabelWithString:@"「更新を確認」で配布サーバーへ接続します。APIキーや録音データは送信しません。"];
    self.statusLabel.frame = NSMakeRect(24, 132, 472, 140);
    [window.contentView addSubview:self.statusLabel];
    NSTextField *note = [NSTextField wrappingLabelWithString:@"設定と録音履歴は保持されます。未署名の開発版のため、更新はmacOSのインストーラーで確認して実行します。"];
    note.font = [NSFont systemFontOfSize:11];
    note.textColor = NSColor.secondaryLabelColor;
    note.frame = NSMakeRect(24, 76, 472, 44);
    [window.contentView addSubview:note];
    self.progress = [[NSProgressIndicator alloc] initWithFrame:NSMakeRect(24, 45, 20, 20)];
    self.progress.style = NSProgressIndicatorStyleSpinning;
    self.progress.displayedWhenStopped = NO;
    [window.contentView addSubview:self.progress];
    self.actionButton = [NSButton buttonWithTitle:@"更新を確認" target:self action:@selector(performAction:)];
    self.actionButton.frame = NSMakeRect(165, 24, 245, 32);
    [window.contentView addSubview:self.actionButton];
    self.closeButton = [NSButton buttonWithTitle:@"閉じる" target:self action:@selector(closeUpdater:)];
    self.closeButton.frame = NSMakeRect(415, 24, 82, 32);
    [window.contentView addSubview:self.closeButton];
    return self;
}
- (void)showUpdater { [self showWindow:nil]; [NSApp activateIgnoringOtherApps:YES]; }
- (void)setWorking:(BOOL)working message:(NSString *)message {
    self.busy = working;
    self.statusLabel.stringValue = message;
    self.actionButton.enabled = !working;
    self.closeButton.title = working ? @"キャンセル" : @"閉じる";
    if (working) [self.progress startAnimation:nil]; else [self.progress stopAnimation:nil];
}
- (void)fetchURL:(NSURL *)url limit:(NSUInteger)limit completion:(void (^)(NSData *, NSError *))completion {
    self.transfer = [VITUpdateTransfer new];
    [self.transfer startURL:url limit:limit completion:completion];
}
- (void)performAction:(id)sender {
    if (self.busy || self.handingOff) return;
    if (self.packageURL) [self beginInstall];
    else if (self.availableRelease) [self downloadRelease];
    else [self checkForUpdates];
}
- (void)checkForUpdates {
    if (!VITUpdateVersionIsValid(self.currentVersion)) {
        [self setWorking:NO message:@"現在のバージョンを確認できません。ダウンロードページから更新してください。"];
        return;
    }
    NSUInteger serial = ++self.serial;
    [self setWorking:YES message:@"最新版を確認しています…"];
    __weak typeof(self) weakSelf = self;
    [self fetchURL:[NSURL URLWithString:@"https://voice-input-tool.pages.dev/update.json"] limit:65536
        completion:^(NSData *data, NSError *error) {
        VITUpdateController *owner = weakSelf;
        if (!owner || serial != owner.serial) return;
        NSDictionary *release = error ? nil : VITValidateUpdateManifest(data, &error);
        if (!release) { [owner setWorking:NO message:error.localizedDescription ?: @"更新確認に失敗しました。再試行してください。"]; return; }
        if (VITCompareUpdateVersions(release[@"version"], owner.currentVersion) != NSOrderedDescending) {
            [owner setWorking:NO message:@"お使いのアプリは最新版です。"];
            return;
        }
        NSOperatingSystemVersion os = NSProcessInfo.processInfo.operatingSystemVersion;
        NSString *osVersion = [NSString stringWithFormat:@"%ld.%ld.%ld", os.majorVersion, os.minorVersion, os.patchVersion];
        if (VITCompareUpdateVersions(osVersion, release[@"minimum_macos"]) == NSOrderedAscending) {
            [owner setWorking:NO message:[NSString stringWithFormat:@"この更新にはmacOS %@以降が必要です。", release[@"minimum_macos"]]];
            return;
        }
        owner.availableRelease = release;
        owner.actionButton.title = @"更新をダウンロード";
        [owner setWorking:NO message:[NSString stringWithFormat:@"バージョン %@ を利用できます。\n\n%@", release[@"version"], release[@"notes"]]];
    }];
}
- (void)downloadRelease {
    NSUInteger serial = ++self.serial;
    [self setWorking:YES message:@"更新をダウンロードしています…"];
    __weak typeof(self) weakSelf = self;
    [self fetchURL:[NSURL URLWithString:self.availableRelease[@"url"]] limit:[self.availableRelease[@"size"] unsignedIntegerValue]
        completion:^(NSData *data, NSError *error) {
        VITUpdateController *owner = weakSelf;
        if (!owner || serial != owner.serial) return;
        if (error || !VITVerifyUpdatePackage(data, owner.availableRelease)) {
            [owner setWorking:NO message:error.localizedDescription ?: @"更新ファイルの検証に失敗しました。インストールは行いません。"];
            return;
        }
        [owner discardDownload];
        NSString *pattern = [NSTemporaryDirectory() stringByAppendingPathComponent:@"VoiceInputTool-update.XXXXXX"];
        char *buffer = strdup(pattern.fileSystemRepresentation);
        char *directory = mkdtemp(buffer);
        if (directory) owner.downloadDirectory = [[NSFileManager defaultManager] stringWithFileSystemRepresentation:directory length:strlen(directory)];
        free(buffer);
        if (!owner.downloadDirectory) { [owner setWorking:NO message:@"更新ファイルの保存先を作成できませんでした。"]; return; }
        NSString *name = [NSString stringWithFormat:@"VoiceInputTool-%@-mac-arm64.pkg", owner.availableRelease[@"version"]];
        NSURL *url = [NSURL fileURLWithPath:[owner.downloadDirectory stringByAppendingPathComponent:name]];
        if (![data writeToURL:url options:NSDataWritingAtomic error:&error]) {
            [owner discardDownload];
            [owner setWorking:NO message:@"更新ファイルを保存できませんでした。空き容量を確認してください。"];
            return;
        }
        chmod(url.fileSystemRepresentation, 0600);
        owner.packageURL = url;
        owner.actionButton.title = @"アプリを終了してインストール";
        [owner setWorking:NO message:@"ダウンロードとSHA-256照合が完了しました。\n\n録音・文字起こし・設定を終えてから、下のボタンで更新してください。macOSのインストーラーが開きます。中止した場合はアプリを開き直してください。"];
    }];
}
- (void)beginInstall {
    if (!self.canInstall || !self.canInstall()) {
        [self setWorking:NO message:@"録音・文字起こし・設定が終了してから、もう一度インストールを押してください。"];
        return;
    }
    // Recheck the private on-disk file immediately before handing it to Installer.
    NSDictionary *attrs = [NSFileManager.defaultManager attributesOfItemAtPath:self.packageURL.path error:nil];
    NSData *data = [attrs[NSFileSize] unsignedLongLongValue] == [self.availableRelease[@"size"] unsignedLongLongValue] ?
        [NSData dataWithContentsOfURL:self.packageURL] : nil;
    if (!VITVerifyUpdatePackage(data, self.availableRelease)) {
        [self discardDownload];
        self.actionButton.title = @"更新をダウンロード";
        [self setWorking:NO message:@"更新ファイルが変更されています。再ダウンロードしてください。"];
        return;
    }
    self.handingOff = YES;
    [self setWorking:YES message:@"音声エンジンの終了を待っています…"];
    self.closeButton.enabled = NO;
    if (!self.install || !self.install(self.packageURL)) [self installationFailed:@"インストーラーを開始できませんでした。再試行してください。"];
}
- (void)installationFailed:(NSString *)message {
    self.handingOff = NO;
    self.closeButton.enabled = YES;
    [self setWorking:NO message:message];
}
- (void)discardDownload {
    if (self.downloadDirectory) [NSFileManager.defaultManager removeItemAtPath:self.downloadDirectory error:nil];
    self.downloadDirectory = nil;
    self.packageURL = nil;
}
- (void)closeUpdater:(id)sender { if (!self.handingOff) [self.window performClose:sender]; }
- (BOOL)windowShouldClose:(NSWindow *)sender { return !self.handingOff; }
- (void)windowWillClose:(NSNotification *)notification {
    self.serial++;
    [self.transfer cancel];
    self.transfer = nil;
    [self discardDownload];
    self.availableRelease = nil;
    self.actionButton.title = @"更新を確認";
    [self setWorking:NO message:@"「更新を確認」で配布サーバーへ接続します。APIキーや録音データは送信しません。"];
}
@end
