// Compile the implementation into this test to exercise transport and UI seams without network/installing.
#import "../native/VITUpdateController.m"

@interface TestUpdater : VITUpdateController
@property(nonatomic, strong) NSMutableArray<NSURL *> *requests;
@property(nonatomic, copy) void (^reply)(NSData *, NSError *);
@end
@implementation TestUpdater
- (void)fetchURL:(NSURL *)url limit:(NSUInteger)limit completion:(void (^)(NSData *, NSError *))completion {
    if (!self.requests) self.requests = [NSMutableArray array];
    [self.requests addObject:url];
    self.reply = completion;
}
@end

static NSData *JSON(NSDictionary *value) {
    return [NSJSONSerialization dataWithJSONObject:value options:0 error:nil];
}

static NSDictionary *Release(NSData *package) {
    unsigned char digest[CC_SHA256_DIGEST_LENGTH];
    CC_SHA256(package.bytes, (CC_LONG)package.length, digest);
    NSMutableString *hash = [NSMutableString string];
    for (NSUInteger i = 0; i < sizeof(digest); i++) [hash appendFormat:@"%02x", digest[i]];
    return @{@"schema_version": @1, @"version": @"1.0.14", @"minimum_macos": @"13.0.0",
        @"architecture": @"arm64", @"url": @"https://voice-input-tool.pages.dev/downloads/VoiceInputTool-1.0.14-mac-arm64.pkg",
        @"sha256": hash, @"size": @(package.length), @"notes": @"アプリ内から最新版を確認し、設定と録音履歴を保持して更新できます。"};
}

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        [NSApplication sharedApplication];
        [NSApp setActivationPolicy:NSApplicationActivationPolicyAccessory];
        NSData *package = [@"xar!test-package" dataUsingEncoding:NSUTF8StringEncoding];
        NSDictionary *release = Release(package);
        NSCAssert(VITUpdateVersionIsValid(@"1.0.14") && !VITUpdateVersionIsValid(@"1.0") &&
                  !VITUpdateVersionIsValid(@"1.0.14\n") && !VITUpdateVersionIsValid(@14), @"Strict versions");
        NSCAssert(VITCompareUpdateVersions(@"1.0.14", @"1.0.9") == NSOrderedDescending, @"Numeric versions");
        NSCAssert(VITCompareUpdateVersions(@"1.0.14", @"1.0.14") == NSOrderedSame, @"Equal versions");
        NSCAssert(VITCompareUpdateVersions(@"1.9.99", @"2.0.0") == NSOrderedAscending, @"Major version ordering");
        NSCAssert(VITValidateUpdateManifest(JSON(release), nil) != nil, @"Valid release");
        NSCAssert(!VITValidateUpdateManifest(nil, nil), @"Empty server response");
        NSCAssert(!VITValidateUpdateManifest([@"<html>fallback</html>" dataUsingEncoding:NSUTF8StringEncoding], nil), @"Reject HTML fallback");
        NSCAssert(!VITValidateUpdateManifest([NSMutableData dataWithLength:65537], nil), @"Manifest size cap");
        NSDictionary *badFields = @{
            @"schema_version": @[@"1", @YES, @2, @1.5],
            @"version": @[@"../1", @"1.0", @"1.0.14\n", NSNull.null],
            @"minimum_macos": @[@"13", @13],
            @"architecture": @[@"x86_64", NSNull.null],
            @"size": @[@0, @YES, @(-1), @1.5, @(VITPackageLimit + 1), @"12"],
            @"sha256": @[@"bad", NSNull.null],
            @"notes": @[@{}, NSNull.null],
            @"url": @[@"http://voice-input-tool.pages.dev/downloads/a.pkg",
                @"https://evil.example/a.pkg", @"https://voice-input-tool.pages.dev.evil.example/a.pkg",
                @"https://voice-input-tool.pages.dev/downloads/VoiceInputTool-1.0.14-mac-arm64.pkg?redirect=x",
                @"file:///tmp/a.pkg", @"https://voice-input-tool.pages.dev/downloads/../a.pkg"]};
        for (NSString *key in badFields) for (id bad in badFields[key]) {
            NSMutableDictionary *invalid = [release mutableCopy]; invalid[key] = bad;
            NSCAssert(!VITValidateUpdateManifest(JSON(invalid), nil), @"Reject invalid %@", key);
        }
        NSCAssert(VITVerifyUpdatePackage(package, release), @"Valid package digest");
        NSCAssert(!VITVerifyUpdatePackage([@"xar!test-packagE" dataUsingEncoding:NSUTF8StringEncoding], release), @"Digest mismatch");
        NSCAssert(!VITVerifyUpdatePackage([NSData data], release), @"Empty package");
        NSData *html = [@"<html>error</html>" dataUsingEncoding:NSUTF8StringEncoding];
        NSCAssert(!VITVerifyUpdatePackage(html, Release(html)), @"Reject non-package even with matching digest");

        __block BOOL idle = YES;
        __block NSInteger installs = 0;
        TestUpdater *updater = [[TestUpdater alloc] initWithCurrentVersion:@"1.0.13"
            canInstallHandler:^{ return idle; }
            installHandler:^BOOL(NSURL *url) { installs++; return NO; }];
        if (argc == 2 && strcmp(argv[1], "--preview") == 0) {
            [updater performAction:nil]; updater.reply(JSON(release), nil);
            [updater showUpdater];
            dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 60 * NSEC_PER_SEC), dispatch_get_main_queue(), ^{ [NSApp terminate:nil]; });
            [NSApp run];
            return 0;
        }
        NSCAssert(updater.requests.count == 0, @"No automatic network requests");
        for (NSView *view in updater.window.contentView.subviews)
            NSCAssert(NSContainsRect(updater.window.contentView.bounds, view.frame), @"Controls fit window");
        [updater performAction:nil];
        NSCAssert(updater.busy && !updater.actionButton.enabled, @"Checking state");
        [updater performAction:nil];
        NSCAssert(updater.requests.count == 1, @"No duplicate check");
        updater.reply(nil, UpdateError(@"offline"));
        NSCAssert(!updater.busy && !updater.availableRelease && installs == 0, @"Network error does not install");
        [updater performAction:nil];
        updater.reply(JSON(release), nil);
        NSCAssert(updater.availableRelease != nil && !updater.busy, @"New release offered");

        if (argc == 2) {
            updater.window.appearance = [NSAppearance appearanceNamed:NSAppearanceNameAqua];
            [updater showUpdater];
            [updater.window display];
            [NSRunLoop.currentRunLoop runUntilDate:[NSDate dateWithTimeIntervalSinceNow:0.1]];
            NSView *view = updater.window.contentView;
            [view displayIfNeeded];
            NSBitmapImageRep *image = [view bitmapImageRepForCachingDisplayInRect:view.bounds];
            [view cacheDisplayInRect:view.bounds toBitmapImageRep:image];
            NSData *png = [image representationUsingType:NSBitmapImageFileTypePNG properties:@{}];
            NSCAssert([png writeToFile:[NSString stringWithUTF8String:argv[1]] atomically:YES], @"UI snapshot saved");
        }

        [updater performAction:nil];
        updater.reply([@"bad" dataUsingEncoding:NSUTF8StringEncoding], nil);
        NSCAssert(!updater.packageURL && installs == 0 && !updater.busy, @"Corrupt download remains retryable");
        [updater performAction:nil]; updater.reply(package, nil);
        NSURL *saved = updater.packageURL;
        NSCAssert(saved != nil && installs == 0, @"Download requires separate install consent");
        NSDictionary *attrs = [NSFileManager.defaultManager attributesOfItemAtPath:updater.downloadDirectory error:nil];
        NSCAssert(([attrs[NSFilePosixPermissions] integerValue] & 0777) == 0700, @"Private directory");
        idle = NO; [updater performAction:nil];
        NSCAssert(installs == 0 && updater.packageURL != nil, @"Recording or settings blocks install");
        idle = YES; [updater performAction:nil];
        NSCAssert(installs == 1 && !updater.handingOff && !updater.busy, @"Failed installer launch keeps app usable");
        [@"xar!test-packagE" writeToURL:saved atomically:YES encoding:NSUTF8StringEncoding error:nil];
        [updater performAction:nil];
        NSCAssert(installs == 1 && !updater.packageURL, @"Disk tampering rejected");
        [updater performAction:nil]; updater.reply(package, nil);
        saved = updater.packageURL;
        NSNotification *close = [NSNotification notificationWithName:NSWindowWillCloseNotification object:updater.window];
        [updater windowWillClose:close];
        NSCAssert(![NSFileManager.defaultManager fileExistsAtPath:saved.path], @"Cancelled package removed");
        [updater performAction:nil];
        void (^lateReply)(NSData *, NSError *) = updater.reply;
        [updater windowWillClose:close]; lateReply(JSON(release), nil);
        NSCAssert(!updater.availableRelease && !updater.busy, @"Late replies after cancellation are ignored");

        for (NSString *version in @[@"1.0.14", @"2.0.0"]) {
            TestUpdater *current = [[TestUpdater alloc] initWithCurrentVersion:version canInstallHandler:nil installHandler:nil];
            [current performAction:nil]; current.reply(JSON(release), nil);
            NSCAssert(!current.availableRelease && !current.packageURL, @"Never downgrade or reinstall same version");
        }

        // Exercise the real transfer delegates: failed HTTP, oversize body, redirects and cancellation.
        __block NSInteger completions = 0;
        __block NSError *failure = nil;
        NSURLSession *dummySession = [NSURLSession sessionWithConfiguration:NSURLSessionConfiguration.ephemeralSessionConfiguration];
        NSURL *testURL = [NSURL URLWithString:@"https://voice-input-tool.pages.dev/update.json"];
        NSURLSessionDataTask *dummyTask = [dummySession dataTaskWithURL:testURL]; // Never resumed.
        VITUpdateTransfer *transfer = [VITUpdateTransfer new];
        transfer.data = [NSMutableData data]; transfer.limit = 4;
        transfer.completion = ^(NSData *data, NSError *error) { completions++; failure = error; };
        [transfer URLSession:dummySession dataTask:dummyTask didReceiveData:package];
        [transfer URLSession:dummySession task:dummyTask didCompleteWithError:nil];
        NSCAssert(completions == 1 && failure, @"Size cap cancels and completes once");
        transfer = [VITUpdateTransfer new]; transfer.limit = 65536;
        transfer.completion = ^(NSData *data, NSError *error) { completions++; failure = error; };
        NSHTTPURLResponse *response = [[NSHTTPURLResponse alloc] initWithURL:[NSURL URLWithString:@"https://voice-input-tool.pages.dev/update.json"] statusCode:404 HTTPVersion:@"HTTP/1.1" headerFields:@{}];
        [transfer URLSession:dummySession dataTask:dummyTask didReceiveResponse:response completionHandler:^(NSURLSessionResponseDisposition value) {
            NSCAssert(value == NSURLSessionResponseCancel, @"Reject non-200 HTTP");
        }];
        NSCAssert(completions == 2 && failure, @"HTTP failure reported");
        transfer = [VITUpdateTransfer new];
        transfer.completion = ^(NSData *data, NSError *error) { completions++; failure = error; };
        [transfer URLSession:dummySession task:dummyTask willPerformHTTPRedirection:response newRequest:[NSURLRequest requestWithURL:testURL] completionHandler:^(NSURLRequest *request) {
            NSCAssert(!request, @"Reject redirects");
        }];
        NSCAssert(completions == 3 && failure, @"Redirect failure reported");
        transfer = [VITUpdateTransfer new];
        transfer.completion = ^(NSData *data, NSError *error) { completions++; failure = error; };
        [transfer cancel];
        NSCAssert(completions == 4 && failure.code == NSURLErrorCancelled, @"Cancel completes");
        [dummySession invalidateAndCancel];
        NSCAssert(!TrustedURL([NSURL URLWithString:@"http://voice-input-tool.pages.dev/update.json"]) &&
                  !TrustedURL([NSURL URLWithString:@"https://user@voice-input-tool.pages.dev/update.json"]), @"HTTPS host policy");
        puts("Native update: versions, schema, HTTPS, checksums, consent, busy state, cancellation, retries and private storage passed");
    }
    return 0;
}
