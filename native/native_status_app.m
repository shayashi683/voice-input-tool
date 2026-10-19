#import <Cocoa/Cocoa.h>
#import <ApplicationServices/ApplicationServices.h>
#import "VITCursorMicIndicator.h"
#import "VITGlobalHotKey.h"
#import "VITNativeSupport.h"
#import "VITSetupGuideController.h"
#import "VITTextPasteController.h"
#import "VITUpdateController.h"

@interface AppDelegate : NSObject <NSApplicationDelegate>
@property(nonatomic, strong) NSStatusItem *statusItem;
@property(nonatomic, strong) NSMenuItem *toggleItem;
@property(nonatomic, strong) NSMenuItem *llmItem;
@property(nonatomic, strong) NSTask *engineTask;
@property(nonatomic, strong) NSTask *settingsTask;
@property(nonatomic, assign) BOOL restartEngineWhenIdle;
@property(nonatomic, strong) NSTimer *outputTimer;
@property(nonatomic, assign) unsigned long long outputOffset;
@property(nonatomic, strong) NSMutableData *pendingOutput;
@property(nonatomic, strong) NSMutableSet<NSString *> *pendingRequests;
@property(nonatomic, assign) NSTimeInterval lastReadyHeartbeat;
@property(nonatomic, assign) NSTimeInterval lastStatusModified;
@property(nonatomic, copy) NSString *currentStatus;
@property(nonatomic, copy) NSString *currentStatusTitle;
@property(nonatomic, assign) NSUInteger statusAnimationFrame;
@property(nonatomic, strong) VITCursorMicIndicator *cursorMicIndicator;
@property(nonatomic, strong) VITGlobalHotKey *globalHotKey;
@property(nonatomic, strong) VITTextPasteController *pasteController;
@property(nonatomic, strong) VITSetupGuideController *setupGuide;
@property(nonatomic, strong) VITUpdateController *updateController;
@property(nonatomic, strong) NSURL *pendingUpdatePackage;
@property(nonatomic, strong) NSDate *updateStartedAt;
@property(nonatomic, assign) NSTimeInterval lastCommandAt;
- (void)toggleRecording:(id)sender;
- (void)toggleRecordingForTargetPid:(pid_t)targetPid;
- (BOOL)showStatusIconIfAvailable;
- (void)setStatusButtonTitle:(NSString *)title;
@end

@implementation AppDelegate

- (void)applicationDidFinishLaunching:(NSNotification *)notification {
    VITNativeLog(@"applicationDidFinishLaunching");
    [NSApp setActivationPolicy:NSApplicationActivationPolicyAccessory];
    self.cursorMicIndicator = [[VITCursorMicIndicator alloc] init];
    __weak AppDelegate *weakSelf = self;
    self.globalHotKey = [[VITGlobalHotKey alloc] initWithHandler:^{
        [weakSelf toggleRecordingForTargetPid:VITCurrentFrontmostApplicationPid()];
    }];
    self.pasteController = [[VITTextPasteController alloc] initWithStatusTitleHandler:^(NSString *title) {
        [weakSelf setStatusButtonTitle:title];
    }];
    BOOL showSetupGuide = VITSetupGuideNeedsPresentation(VITResolvedWorkDir());
    if (!showSetupGuide) [self requestAccessibilityIfNeeded];
    [self setupStatusItem];
    [self registerConfiguredHotKey];
    [self prepareOutputPolling];
    [self startEngine];
    if (showSetupGuide) [self openSetupGuide:nil];
}

- (void)requestAccessibilityIfNeeded {
    NSDictionary *options = @{(__bridge id)kAXTrustedCheckOptionPrompt: @YES};
    BOOL trusted = AXIsProcessTrustedWithOptions((__bridge CFDictionaryRef)options);
    VITNativeLog([NSString stringWithFormat:@"accessibility trusted: %@", trusted ? @"yes" : @"no"]);
}

- (void)setupStatusItem {
    VITNativeLog(@"setupStatusItem start");
    self.statusItem = [[NSStatusBar systemStatusBar] statusItemWithLength:NSVariableStatusItemLength];
    if (![self showStatusIconIfAvailable]) {
        [self setStatusButtonTitle:@"🎙"];
    }

    NSMenu *menu = [[NSMenu alloc] initWithTitle:@"Voice Input Tool"];

    self.toggleItem = [[NSMenuItem alloc] initWithTitle:@"録音 開始/停止"
                                                 action:@selector(toggleRecording:)
                                          keyEquivalent:@""];
    self.toggleItem.target = self;
    [menu addItem:self.toggleItem];

    self.llmItem = [[NSMenuItem alloc] initWithTitle:@"LLM補正: --"
                                             action:@selector(toggleLLM:)
                                      keyEquivalent:@""];
    self.llmItem.target = self;
    [menu addItem:self.llmItem];

    NSMenuItem *historyItem = [[NSMenuItem alloc] initWithTitle:@"録音履歴を開く"
                                                      action:@selector(openRecordingHistory:)
                                               keyEquivalent:@""];
    historyItem.target = self;
    [menu addItem:historyItem];

    NSString *version = [[NSBundle mainBundle] objectForInfoDictionaryKey:@"CFBundleShortVersionString"];
    if ([version length] > 0) {
        NSMenuItem *versionItem = [[NSMenuItem alloc] initWithTitle:[NSString stringWithFormat:@"バージョン %@", version]
                                                            action:nil
                                                     keyEquivalent:@""];
        versionItem.enabled = NO;
        [menu addItem:versionItem];
    }

    NSMenuItem *updateItem = [[NSMenuItem alloc] initWithTitle:@"更新を確認…"
        action:@selector(openUpdater:) keyEquivalent:@""];
    updateItem.target = self;
    [menu addItem:updateItem];

    NSMenuItem *permissionItem = [[NSMenuItem alloc] initWithTitle:@"アクセシビリティ許可を確認"
                                                            action:@selector(checkAccessibility:)
                                                     keyEquivalent:@""];
    permissionItem.target = self;
    [menu addItem:permissionItem];

    NSMenuItem *settingsItem = [[NSMenuItem alloc] initWithTitle:@"設定..."
                                                          action:@selector(openSettings:)
                                                   keyEquivalent:@""];
    settingsItem.target = self;
    [menu addItem:settingsItem];

    NSMenuItem *setupItem = [[NSMenuItem alloc] initWithTitle:@"はじめての設定..."
                                                    action:@selector(openSetupGuide:)
                                             keyEquivalent:@""];
    setupItem.target = self;
    [menu addItem:setupItem];

    [menu addItem:[NSMenuItem separatorItem]];

    NSMenuItem *quitItem = [[NSMenuItem alloc] initWithTitle:@"終了"
                                                      action:@selector(quit:)
                                               keyEquivalent:@""];
    quitItem.target = self;
    [menu addItem:quitItem];

    self.statusItem.menu = menu;
    VITNativeLog(@"setupStatusItem complete");
}

- (BOOL)showStatusIconIfAvailable {
    NSImage *icon = VITStatusBarIcon();
    if (!icon || !self.statusItem.button) {
        return NO;
    }

    self.statusItem.button.title = @"";
    self.statusItem.button.image = icon;
    self.statusItem.button.imagePosition = NSImageOnly;
    self.statusItem.button.toolTip = @"Voice Input Tool";
    return YES;
}

- (void)setStatusButtonTitle:(NSString *)title {
    self.statusItem.button.image = nil;
    self.statusItem.button.imagePosition = NSNoImage;
    self.statusItem.button.title = title ?: @"";
    self.statusItem.button.toolTip = @"Voice Input Tool";
}

- (NSFileHandle *)appendHandleForPath:(NSString *)path {
    VITEnsureParentDirectory(path);
    NSFileManager *fm = [NSFileManager defaultManager];
    if (![fm fileExistsAtPath:path]) {
        [fm createFileAtPath:path contents:nil attributes:nil];
    }
    NSFileHandle *handle = [NSFileHandle fileHandleForWritingAtPath:path];
    if (handle) {
        [handle seekToEndOfFile];
    }
    return handle;
}

- (NSDictionary *)loadConfig {
    NSString *configPath = VITWorkDirPath(@"config.json");
    NSData *data = [NSData dataWithContentsOfFile:configPath];
    if (!data) {
        return @{};
    }

    NSError *error = nil;
    id json = [NSJSONSerialization JSONObjectWithData:data options:0 error:&error];
    if (![json isKindOfClass:[NSDictionary class]]) {
        VITNativeLog([NSString stringWithFormat:@"config parse failed: %@", error]);
        return @{};
    }
    return (NSDictionary *)json;
}

- (void)registerConfiguredHotKey {
    NSDictionary *config = [self loadConfig];
    NSString *hotKey = [config objectForKey:@"hotkey_record"];
    if (![hotKey isKindOfClass:[NSString class]] || [hotKey length] == 0) {
        hotKey = @"<ctrl>+<shift>+<space>";
    }

    [self.globalHotKey registerHotKey:hotKey];
}

- (void)startEngine {
    if (self.engineTask && self.engineTask.isRunning) {
        return;
    }
    VITNativeLog(@"startEngine");

    NSTask *task = [[NSTask alloc] init];
    task.executableURL = [NSURL fileURLWithPath:VITPythonPath()];
    task.arguments = @[VITScriptPath(), @"--headless"];
    task.currentDirectoryURL = [NSURL fileURLWithPath:VITResolvedWorkDir()];
    NSMutableDictionary *environment = VITTaskEnvironment();
    NSDictionary *commandAttributes = [[NSFileManager defaultManager] attributesOfItemAtPath:VITCommandFilePath() error:nil];
    environment[@"VOICE_INPUT_COMMAND_OFFSET"] = [NSString stringWithFormat:@"%llu", [commandAttributes[NSFileSize] unsignedLongLongValue]];
    task.environment = environment;
    task.standardOutput = [self appendHandleForPath:VITWorkDirPath(@"logs/native-engine.out.log")];
    task.standardError = [self appendHandleForPath:VITWorkDirPath(@"logs/native-engine.err.log")];
    task.terminationHandler = ^(NSTask *finishedTask) {
        VITNativeLog([NSString stringWithFormat:@"engine exited: status=%d", finishedTask.terminationStatus]);
    };
    self.engineTask = task;

    NSError *error = nil;
    if (![task launchAndReturnError:&error]) {
        NSLog(@"Voice Input Tool engine launch failed: %@", error);
        VITNativeLog([NSString stringWithFormat:@"engine launch failed: %@", error]);
    } else {
        VITNativeLog(@"engine launch complete");
    }
}

- (void)restartEngine {
    VITNativeLog(@"restartEngine");
    if (self.engineTask && self.engineTask.isRunning) {
        [self sendCommand:@"quit"];
        [self.engineTask terminate];
    }
    self.engineTask = nil;
    [self startEngine];
}

- (void)openRecordingHistory:(id)sender {
    NSString *path = VITWorkDirPath(@"recordings");
    [[NSFileManager defaultManager] createDirectoryAtPath:path withIntermediateDirectories:YES
                                             attributes:@{NSFilePosixPermissions: @0700} error:nil];
    [[NSWorkspace sharedWorkspace] openURL:[NSURL fileURLWithPath:path]];
}

- (void)prepareOutputPolling {
    self.pendingOutput = [NSMutableData data];
    self.pendingRequests = [NSMutableSet set];
    VITEnsureParentDirectory(VITOutputFilePath());
    if (![[NSFileManager defaultManager] fileExistsAtPath:VITOutputFilePath()]) {
        [[NSFileManager defaultManager] createFileAtPath:VITOutputFilePath() contents:nil attributes:nil];
    }

    NSDictionary *attrs = [[NSFileManager defaultManager] attributesOfItemAtPath:VITOutputFilePath() error:nil];
    self.outputOffset = [[attrs objectForKey:NSFileSize] unsignedLongLongValue];
    self.outputTimer = [NSTimer scheduledTimerWithTimeInterval:0.2
                                                        target:self
                                                      selector:@selector(pollOutputFile:)
                                                      userInfo:nil
                                                       repeats:YES];
    [self writePasteReadyHeartbeat];
    [self pollStatusFile];
    VITNativeLog([NSString stringWithFormat:@"output polling start: offset=%llu", self.outputOffset]);
}

- (void)pollOutputFile:(NSTimer *)timer {
    [self writePasteReadyHeartbeatIfNeeded];
    [self pollStatusFile];
    [self animateStatusIndicator];

    NSDictionary *attrs = [[NSFileManager defaultManager] attributesOfItemAtPath:VITOutputFilePath() error:nil];
    unsigned long long size = [[attrs objectForKey:NSFileSize] unsignedLongLongValue];
    if (size < self.outputOffset) {
        self.outputOffset = 0;
        [self.pendingOutput setLength:0];
    }
    if (size == self.outputOffset) {
        return;
    }

    NSFileHandle *handle = [NSFileHandle fileHandleForReadingAtPath:VITOutputFilePath()];
    if (!handle) {
        return;
    }

    [handle seekToFileOffset:self.outputOffset];
    NSData *data = [handle readDataToEndOfFile];
    self.outputOffset += [data length];
    [handle closeFile];

    // Decode only complete UTF-8 lines. A poll may split a JSON object or even
    // one Japanese character across two reads.
    [self.pendingOutput appendData:data];
    const unsigned char newline = '\n';
    NSData *delimiter = [NSData dataWithBytes:&newline length:1];
    while (YES) {
        NSRange range = [self.pendingOutput rangeOfData:delimiter options:0
                                                range:NSMakeRange(0, self.pendingOutput.length)];
        if (range.location == NSNotFound) break;
        NSData *lineData = [self.pendingOutput subdataWithRange:NSMakeRange(0, range.location)];
        [self.pendingOutput replaceBytesInRange:NSMakeRange(0, range.location + 1) withBytes:NULL length:0];
        NSString *line = [[NSString alloc] initWithData:lineData encoding:NSUTF8StringEncoding];
        if (line.length > 0) [self handleOutputLine:line];
    }
}

- (void)pollStatusFile {
    NSDictionary *attrs = [[NSFileManager defaultManager] attributesOfItemAtPath:VITStatusFilePath() error:nil];
    NSDate *modified = [attrs objectForKey:NSFileModificationDate];
    if (!modified) {
        return;
    }

    NSTimeInterval modifiedTime = [modified timeIntervalSince1970];
    if (modifiedTime <= self.lastStatusModified) {
        return;
    }
    self.lastStatusModified = modifiedTime;

    NSData *data = [NSData dataWithContentsOfFile:VITStatusFilePath()];
    if (!data) {
        return;
    }

    NSError *error = nil;
    id json = [NSJSONSerialization JSONObjectWithData:data options:0 error:&error];
    if (![json isKindOfClass:[NSDictionary class]]) {
        VITNativeLog([NSString stringWithFormat:@"status parse failed: %@", error]);
        return;
    }

    NSDictionary *payload = (NSDictionary *)json;
    NSString *title = [payload objectForKey:@"title"];
    NSString *recordTitle = [payload objectForKey:@"record_title"];
    NSString *llmTitle = [payload objectForKey:@"llm_title"];
    NSString *status = [payload objectForKey:@"status"];

    NSString *previousStatus = self.currentStatus;
    if ([status isKindOfClass:[NSString class]]) {
        self.currentStatus = status;
    }

    if ([self.currentStatus isEqualToString:@"hearing"]) {
        if (![previousStatus isEqualToString:@"hearing"]) {
            self.statusAnimationFrame = 0;
        }
        [self animateStatusIndicator];
    }

    if ([title isKindOfClass:[NSString class]] && [title length] > 0) {
        self.currentStatusTitle = title;
        if (![self.currentStatus isEqualToString:@"hearing"]) {
            if (![self.currentStatus isEqualToString:@"idle"] || ![self showStatusIconIfAvailable]) {
                [self setStatusButtonTitle:title];
            }
        }
    }
    if ([recordTitle isKindOfClass:[NSString class]] && [recordTitle length] > 0) {
        self.toggleItem.title = recordTitle;
    }
    if ([llmTitle isKindOfClass:[NSString class]] && [llmTitle length] > 0) {
        self.llmItem.title = llmTitle;
    }
    [self.cursorMicIndicator updateForStatus:self.currentStatus];
    if (self.restartEngineWhenIdle && [self.currentStatus isEqualToString:@"idle"]) {
        self.restartEngineWhenIdle = NO;
        [self restartEngine];
    }
    VITNativeLog([NSString stringWithFormat:@"status updated: %@", status ?: @"unknown"]);
}

- (void)animateStatusIndicator {
    if (![self.currentStatus isEqualToString:@"hearing"]) {
        return;
    }
    NSArray<NSString *> *frames = @[@"● 聞き取り中", @"●● 聞き取り中", @"●●● 聞き取り中"];
    [self setStatusButtonTitle:[frames objectAtIndex:(self.statusAnimationFrame % [frames count])]];
    self.statusAnimationFrame += 1;
}

- (void)writePasteReadyHeartbeatIfNeeded {
    NSTimeInterval now = [[NSDate date] timeIntervalSince1970];
    if (now - self.lastReadyHeartbeat >= 2.0) {
        [self writePasteReadyHeartbeat];
    }
}

- (void)writePasteReadyHeartbeat {
    self.lastReadyHeartbeat = [[NSDate date] timeIntervalSince1970];
    VITEnsureParentDirectory(VITPasteReadyPath());
    NSString *content = [NSString stringWithFormat:@"%f\n", self.lastReadyHeartbeat];
    [content writeToFile:VITPasteReadyPath() atomically:YES encoding:NSUTF8StringEncoding error:nil];
}

- (void)handleOutputLine:(NSString *)line {
    NSData *data = [line dataUsingEncoding:NSUTF8StringEncoding];
    NSError *error = nil;
    id json = [NSJSONSerialization JSONObjectWithData:data options:0 error:&error];
    if (![json isKindOfClass:[NSDictionary class]]) {
        VITNativeLog([NSString stringWithFormat:@"output parse failed: %@", error]);
        return;
    }

    NSDictionary *payload = (NSDictionary *)json;
    NSString *text = [payload objectForKey:@"text"];
    NSNumber *pidNumber = [payload objectForKey:@"pid"];
    if (![text isKindOfClass:[NSString class]] || [text length] == 0) {
        VITNativeLog(@"output ignored: empty text");
        return;
    }

    pid_t pid = 0;
    if ([pidNumber respondsToSelector:@selector(intValue)]) {
        pid = [pidNumber intValue];
    }
    NSString *requestID = payload[@"request_id"];
    if (![requestID isKindOfClass:[NSString class]] || ![[NSUUID alloc] initWithUUIDString:requestID]) {
        requestID = nil; // Compatibility with older engines.
    }
    NSString *receiptPath = requestID ? VITWorkDirPath([NSString stringWithFormat:@"logs/delivery-receipts/%@.json", requestID]) : nil;
    if (requestID && ([self.pendingRequests containsObject:requestID] ||
                      [[NSFileManager defaultManager] fileExistsAtPath:receiptPath])) return;
    if (requestID) [self.pendingRequests addObject:requestID];
    void (^completion)(NSString *) = ^(NSString *status) {
        if (!requestID) return;
        NSDictionary *receipt = @{@"request_id": requestID, @"status": status};
        NSData *receiptData = [NSJSONSerialization dataWithJSONObject:receipt options:0 error:nil];
        VITEnsureParentDirectory(receiptPath);
        NSError *writeError = nil;
        if (![receiptData writeToFile:receiptPath options:NSDataWritingAtomic error:&writeError]) {
            VITNativeLog([NSString stringWithFormat:@"receipt write failed: %@", writeError]);
        } else {
            [[NSFileManager defaultManager] setAttributes:@{NSFilePosixPermissions: @0600}
                                             ofItemAtPath:receiptPath error:nil];
            [self.pendingRequests removeObject:requestID];
        }
    };
    NSNumber *expiresAt = payload[@"expires_at"];
    NSTimeInterval deadline = [expiresAt respondsToSelector:@selector(doubleValue)] ? [expiresAt doubleValue] : 0;
    [self.pasteController pasteText:text targetPid:pid expiresAt:deadline completion:completion];
}

- (BOOL)sendCommand:(NSString *)command targetPid:(pid_t)targetPid {
    NSFileHandle *handle = [self appendHandleForPath:VITCommandFilePath()];
    if (!handle) {
        return NO;
    }

    NSMutableDictionary *payload = [@{
        @"command": command,
        @"created_at": @([[NSDate date] timeIntervalSince1970]),
    } mutableCopy];
    if (targetPid > 0) {
        [payload setObject:@(targetPid) forKey:@"target_pid"];
    }

    NSError *error = nil;
    NSData *jsonData = [NSJSONSerialization dataWithJSONObject:payload options:0 error:&error];
    NSString *line = nil;
    if (jsonData) {
        line = [[NSString alloc] initWithData:jsonData encoding:NSUTF8StringEncoding];
    } else {
        VITNativeLog([NSString stringWithFormat:@"command json failed: %@", error]);
        line = command;
    }
    NSData *data = [[line stringByAppendingString:@"\n"] dataUsingEncoding:NSUTF8StringEncoding];
    [handle writeData:data];
    [handle closeFile];
    self.lastCommandAt = NSDate.date.timeIntervalSince1970;
    return YES;
}

- (void)toggleRecording:(id)sender {
    [self toggleRecordingForTargetPid:VITCurrentFrontmostApplicationPid()];
}

- (void)toggleRecordingForTargetPid:(pid_t)targetPid {
    if (self.pendingUpdatePackage) return;
    VITNativeLog([NSString stringWithFormat:@"toggleRecording target_pid=%d", targetPid]);
    BOOL wasRunning = self.engineTask && self.engineTask.isRunning;
    [self startEngine];
    if (wasRunning) {
        [self sendCommand:@"toggle" targetPid:targetPid];
        return;
    }

    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(0.8 * NSEC_PER_SEC)), dispatch_get_main_queue(), ^{
        [self sendCommand:@"toggle" targetPid:targetPid];
    });
}

- (BOOL)sendCommand:(NSString *)command {
    return [self sendCommand:command targetPid:0];
}

- (void)toggleLLM:(id)sender {
    if (self.pendingUpdatePackage) return;
    VITNativeLog(@"toggleLLM");
    BOOL wasRunning = self.engineTask && self.engineTask.isRunning;
    [self startEngine];
    if (wasRunning) {
        [self sendCommand:@"toggle_llm"];
        return;
    }

    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(0.8 * NSEC_PER_SEC)), dispatch_get_main_queue(), ^{
        [self sendCommand:@"toggle_llm"];
    });
}

- (void)checkAccessibility:(id)sender {
    VITNativeLog(@"checkAccessibility");
    [self requestAccessibilityIfNeeded];
    VITOpenAccessibilitySettings();
}

- (void)openSettings:(id)sender {
    if (self.pendingUpdatePackage) return;
    VITNativeLog(@"openSettings");
    if (self.settingsTask.isRunning) {
        NSRunningApplication *settingsApp = [NSRunningApplication runningApplicationWithProcessIdentifier:self.settingsTask.processIdentifier];
        [settingsApp activateWithOptions:NSApplicationActivateIgnoringOtherApps];
        return;
    }
    NSDictionary *settingsBefore = [self settingsFileVersions];
    NSTask *task = [[NSTask alloc] init];
    self.settingsTask = task;
    task.executableURL = [NSURL fileURLWithPath:VITPythonPath()];
    task.arguments = @[VITScriptPath(), @"--settings"];
    task.currentDirectoryURL = [NSURL fileURLWithPath:VITResolvedWorkDir()];
    task.environment = VITTaskEnvironment();
    task.standardOutput = [self appendHandleForPath:VITWorkDirPath(@"logs/native-settings.out.log")];
    task.standardError = [self appendHandleForPath:VITWorkDirPath(@"logs/native-settings.err.log")];

    __weak AppDelegate *weakSelf = self;
    task.terminationHandler = ^(NSTask *finishedTask) {
        VITNativeLog([NSString stringWithFormat:@"settings exited: status=%d", finishedTask.terminationStatus]);
        dispatch_async(dispatch_get_main_queue(), ^{
            AppDelegate *strongSelf = weakSelf;
            if (strongSelf) {
                if (strongSelf.settingsTask == finishedTask) strongSelf.settingsTask = nil;
                [strongSelf.setupGuide refreshConfiguredHotKey];
                // The shortcut is suspended while settings is open so the
                // capture field can receive the currently configured keys too.
                [strongSelf registerConfiguredHotKey];
                if (![settingsBefore isEqual:[strongSelf settingsFileVersions]]) {
                    if (!strongSelf.engineTask.isRunning || [strongSelf.currentStatus isEqualToString:@"idle"]) {
                        [strongSelf restartEngine];
                    } else {
                        strongSelf.restartEngineWhenIdle = YES;
                    }
                }
            }
        });
    };

    [self.globalHotKey invalidate];
    VITNativeLog(@"native hotkey suspended for settings");
    NSError *error = nil;
    if (![task launchAndReturnError:&error]) {
        self.settingsTask = nil;
        [self registerConfiguredHotKey];
        VITNativeLog([NSString stringWithFormat:@"settings launch failed: %@", error]);
    }
}

- (NSDictionary *)settingsFileVersions {
    // Only inspect metadata. The native shell never reads an API-key value.
    NSMutableDictionary *versions = [NSMutableDictionary dictionary];
    for (NSString *filename in @[@"config.json", @".env"]) {
        NSDictionary *attributes = [NSFileManager.defaultManager attributesOfItemAtPath:VITWorkDirPath(filename) error:nil];
        versions[filename] = @[
            attributes[NSFileModificationDate] ?: NSNull.null,
            attributes[NSFileSystemFileNumber] ?: NSNull.null,
            attributes[NSFileSize] ?: NSNull.null,
        ];
    }
    return versions;
}

- (void)openSetupGuide:(id)sender {
    if (self.pendingUpdatePackage) return;
    (void)sender;
    if (!self.setupGuide) {
        __weak AppDelegate *weakSelf = self;
        self.setupGuide = [[VITSetupGuideController alloc] initWithWorkDirectory:VITResolvedWorkDir()
            settingsHandler:^{ [weakSelf openSettings:nil]; }
            accessibilityHandler:^{ [weakSelf checkAccessibility:nil]; }];
    }
    [self.setupGuide showGuide];
}

- (BOOL)canInstallUpdate {
    // Consume output already written before accepting an idle status; do not
    // quit between the engine writing its result and the native paste callback.
    [self pollOutputFile:nil];
    return !self.pendingUpdatePackage && !self.settingsTask.isRunning && !self.restartEngineWhenIdle &&
        [self.currentStatus isEqualToString:@"idle"] && self.pendingRequests.count == 0 && self.pendingOutput.length == 0 &&
        NSDate.date.timeIntervalSince1970 - self.lastCommandAt > 1.0;
}

- (void)openUpdater:(id)sender {
    if (!self.updateController) {
        __weak AppDelegate *weakSelf = self;
        NSString *version = [NSBundle.mainBundle objectForInfoDictionaryKey:@"CFBundleShortVersionString"];
        self.updateController = [[VITUpdateController alloc] initWithCurrentVersion:version
            canInstallHandler:^{ return [weakSelf canInstallUpdate]; }
            installHandler:^BOOL(NSURL *url) { return [weakSelf installUpdateAtURL:url]; }];
    }
    [self.updateController showUpdater];
}

- (BOOL)installUpdateAtURL:(NSURL *)url {
    if (![self canInstallUpdate]) return NO;
    self.pendingUpdatePackage = url;
    self.updateStartedAt = NSDate.date;
    [self.globalHotKey invalidate];
    // Let the engine close its microphone and flush state; never force-kill for updates.
    [self sendCommand:@"quit"];
    [self finishUpdateWhenEngineStops];
    return YES;
}

- (BOOL)openUpdateInstaller:(NSURL *)url {
    return [NSWorkspace.sharedWorkspace openURL:url];
}

- (void)terminateAfterUpdateHandoff { [NSApp terminate:nil]; }

- (void)finishUpdateWhenEngineStops {
    if (!self.pendingUpdatePackage) return;
    if (self.engineTask.isRunning) {
        if ([NSDate.date timeIntervalSinceDate:self.updateStartedAt] < 10) {
            dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(0.1 * NSEC_PER_SEC)), dispatch_get_main_queue(), ^{
                [self finishUpdateWhenEngineStops];
            });
            return;
        }
        self.pendingUpdatePackage = nil;
        [self registerConfiguredHotKey];
        [self.updateController installationFailed:@"音声エンジンの終了を確認できませんでした。更新は行っていません。アプリを開き直して再試行してください。"];
        return;
    }
    if (![self openUpdateInstaller:self.pendingUpdatePackage]) {
        self.pendingUpdatePackage = nil;
        [self startEngine];
        [self registerConfiguredHotKey];
        [self.updateController installationFailed:@"macOSのインストーラーを開けませんでした。更新は行っていません。"];
        return;
    }
    [self terminateAfterUpdateHandoff];
}

- (void)quit:(id)sender {
    VITNativeLog(@"quit");
    [self sendCommand:@"quit"];
    if (self.engineTask && self.engineTask.isRunning) {
        [self.engineTask terminate];
    }
    [NSApp terminate:nil];
}

- (void)applicationWillTerminate:(NSNotification *)notification {
    VITNativeLog(@"applicationWillTerminate");
    [self.outputTimer invalidate];
    [self.cursorMicIndicator hide];
    [[NSFileManager defaultManager] removeItemAtPath:VITPasteReadyPath() error:nil];
    [self.globalHotKey invalidate];
    [self sendCommand:@"quit"];
    if (self.engineTask && self.engineTask.isRunning) {
        [self.engineTask terminate];
    }
}

@end

int main(int argc, const char * argv[]) {
    (void)argc;
    (void)argv;
    @autoreleasepool {
        VITNativeLog(@"main start");
        NSApplication *app = [NSApplication sharedApplication];
        VITNativeLog(@"sharedApplication complete");
        AppDelegate *delegate = [[AppDelegate alloc] init];
        app.delegate = delegate;
        [app run];
    }
    return 0;
}
