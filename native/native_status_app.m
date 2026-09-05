#import <Cocoa/Cocoa.h>
#import <ApplicationServices/ApplicationServices.h>
#import <Carbon/Carbon.h>
#include <unistd.h>

// リポジトリの場所は .app の中からは辿れないため、ビルド時に
// -DVIT_WORK_DIR='"/path/to/repo"' で埋め込む（packaging/build_app.sh が渡す）。
// 埋め込みが無い場合や .app だけを別マシンへ持ってきた場合に備えて、
// 環境変数と既定の配置場所も順に見る
#ifndef VIT_WORK_DIR
#define VIT_WORK_DIR ""
#endif

static NSString * const WorkDirEnvKey = @"VOICE_INPUT_TOOL_DIR";

static BOOL LooksLikeWorkDir(NSString *path) {
    if ([path length] == 0) {
        return NO;
    }
    NSString *script = [path stringByAppendingPathComponent:@"voice_input.py"];
    return [[NSFileManager defaultManager] fileExistsAtPath:script];
}

static NSString *WorkDir(void) {
    static NSString *resolved = nil;
    static dispatch_once_t onceToken;
    dispatch_once(&onceToken, ^{
        NSString *fromEnv = [[[NSProcessInfo processInfo] environment] objectForKey:WorkDirEnvKey];
        NSString *fallback = [NSHomeDirectory() stringByAppendingPathComponent:@"voice-input-tool"];
        NSArray<NSString *> *candidates = @[
            fromEnv ?: @"",   // 開発中に別のチェックアウトを指したいとき
            @VIT_WORK_DIR,    // ビルドしたマシンでのリポジトリの場所
            fallback,         // READMEの既定の配置
        ];
        for (NSString *candidate in candidates) {
            NSString *path = [candidate stringByExpandingTildeInPath];
            if (LooksLikeWorkDir(path)) {
                resolved = [path copy];
                return;
            }
        }
        // どれも見つからない場合でもログ出力先などは必要なので既定値を使う
        resolved = [fallback copy];
    });
    return resolved;
}

static NSString *WorkPath(NSString *relative) {
    return [WorkDir() stringByAppendingPathComponent:relative];
}

static NSString *PythonPath(void) { return WorkPath(@".venv-framework/bin/python3"); }
static NSString *ScriptPath(void) { return WorkPath(@"voice_input.py"); }
static NSString *CommandFilePath(void) { return WorkPath(@"logs/voice-input-command.txt"); }
static NSString *OutputFilePath(void) { return WorkPath(@"logs/voice-input-output.jsonl"); }
static NSString *StatusFilePath(void) { return WorkPath(@"logs/voice-input-status.json"); }
static NSString *PasteReadyPath(void) { return WorkPath(@"logs/native-paste-ready.txt"); }

static void EnsureParentDirectory(NSString *path) {
    NSString *directory = [path stringByDeletingLastPathComponent];
    [[NSFileManager defaultManager] createDirectoryAtPath:directory
                              withIntermediateDirectories:YES
                                               attributes:nil
                                                    error:nil];
}

static void NativeLog(NSString *message) {
    NSString *path = WorkPath(@"logs/native-status.log");
    EnsureParentDirectory(path);
    NSString *line = [NSString stringWithFormat:@"%@ %@\n", [NSDate date], message];
    NSFileHandle *handle = [NSFileHandle fileHandleForWritingAtPath:path];
    if (!handle) {
        [[NSFileManager defaultManager] createFileAtPath:path contents:nil attributes:nil];
        handle = [NSFileHandle fileHandleForWritingAtPath:path];
    }
    [handle seekToEndOfFile];
    [handle writeData:[line dataUsingEncoding:NSUTF8StringEncoding]];
    [handle closeFile];
}

static pid_t CurrentFrontmostApplicationPid(void) {
    NSRunningApplication *frontmost = [[NSWorkspace sharedWorkspace] frontmostApplication];
    if (!frontmost) {
        return 0;
    }

    pid_t pid = [frontmost processIdentifier];
    return pid == getpid() ? 0 : pid;
}

@interface AppDelegate : NSObject <NSApplicationDelegate>
@property(nonatomic, strong) NSStatusItem *statusItem;
@property(nonatomic, strong) NSMenuItem *toggleItem;
@property(nonatomic, strong) NSMenuItem *llmItem;
@property(nonatomic, strong) NSTask *engineTask;
@property(nonatomic, strong) NSTimer *outputTimer;
@property(nonatomic, assign) unsigned long long outputOffset;
@property(nonatomic, assign) NSTimeInterval lastReadyHeartbeat;
@property(nonatomic, assign) NSTimeInterval lastStatusModified;
@property(nonatomic, copy) NSString *currentStatus;
@property(nonatomic, copy) NSString *currentStatusTitle;
@property(nonatomic, assign) NSUInteger statusAnimationFrame;
@property(nonatomic, assign) EventHotKeyRef hotKeyRef;
@property(nonatomic, assign) EventHandlerRef hotKeyHandlerRef;
- (void)toggleRecording:(id)sender;
- (void)toggleRecordingForTargetPid:(pid_t)targetPid;
@end

static OSStatus HotKeyPressedHandler(EventHandlerCallRef nextHandler, EventRef event, void *userData) {
    AppDelegate *delegate = (__bridge AppDelegate *)userData;
    [delegate toggleRecordingForTargetPid:CurrentFrontmostApplicationPid()];
    return noErr;
}

@implementation AppDelegate

- (void)applicationDidFinishLaunching:(NSNotification *)notification {
    NativeLog(@"applicationDidFinishLaunching");
    NativeLog([NSString stringWithFormat:@"work dir: %@%@",
               WorkDir(),
               LooksLikeWorkDir(WorkDir()) ? @"" : @" (voice_input.py が見つかりません)"]);
    [NSApp setActivationPolicy:NSApplicationActivationPolicyAccessory];
    [self requestAccessibilityIfNeeded];
    [self setupStatusItem];
    [self registerConfiguredHotKey];
    [self prepareOutputPolling];
    [self startEngine];
}

- (void)requestAccessibilityIfNeeded {
    NSDictionary *options = @{(__bridge id)kAXTrustedCheckOptionPrompt: @YES};
    BOOL trusted = AXIsProcessTrustedWithOptions((__bridge CFDictionaryRef)options);
    NativeLog([NSString stringWithFormat:@"accessibility trusted: %@", trusted ? @"yes" : @"no"]);
}

- (void)setupStatusItem {
    NativeLog(@"setupStatusItem start");
    self.statusItem = [[NSStatusBar systemStatusBar] statusItemWithLength:NSVariableStatusItemLength];
    self.statusItem.button.title = @"🎙";

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

    [menu addItem:[NSMenuItem separatorItem]];

    NSMenuItem *quitItem = [[NSMenuItem alloc] initWithTitle:@"終了"
                                                      action:@selector(quit:)
                                               keyEquivalent:@""];
    quitItem.target = self;
    [menu addItem:quitItem];

    self.statusItem.menu = menu;
    NativeLog(@"setupStatusItem complete");
}

- (NSFileHandle *)appendHandleForPath:(NSString *)path {
    EnsureParentDirectory(path);
    NSFileManager *fm = [NSFileManager defaultManager];
    if (![fm fileExistsAtPath:path]) {
        [fm createFileAtPath:path contents:nil attributes:nil];
    }
    NSFileHandle *handle = [NSFileHandle fileHandleForWritingAtPath:path];
    [handle seekToEndOfFile];
    return handle;
}

- (NSDictionary *)loadConfig {
    NSString *configPath = WorkPath(@"config.json");
    NSData *data = [NSData dataWithContentsOfFile:configPath];
    if (!data) {
        return @{};
    }

    NSError *error = nil;
    id json = [NSJSONSerialization JSONObjectWithData:data options:0 error:&error];
    if (![json isKindOfClass:[NSDictionary class]]) {
        NativeLog([NSString stringWithFormat:@"config parse failed: %@", error]);
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

    UInt32 keyCode = 0;
    UInt32 modifiers = 0;
    if (![self parseHotKey:hotKey keyCode:&keyCode modifiers:&modifiers]) {
        NativeLog([NSString stringWithFormat:@"native hotkey parse failed: %@", hotKey]);
        return;
    }

    if (self.hotKeyRef) {
        UnregisterEventHotKey(self.hotKeyRef);
        self.hotKeyRef = NULL;
    }
    if (!self.hotKeyHandlerRef) {
        EventTypeSpec eventType = {kEventClassKeyboard, kEventHotKeyPressed};
        OSStatus handlerStatus = InstallEventHandler(
            GetApplicationEventTarget(),
            HotKeyPressedHandler,
            1,
            &eventType,
            (__bridge void *)self,
            &_hotKeyHandlerRef
        );
        if (handlerStatus != noErr) {
            NativeLog([NSString stringWithFormat:@"native hotkey handler failed: %d", handlerStatus]);
            return;
        }
    }

    EventHotKeyID hotKeyId;
    hotKeyId.signature = 'VITK';
    hotKeyId.id = 1;
    OSStatus status = RegisterEventHotKey(
        keyCode,
        modifiers,
        hotKeyId,
        GetApplicationEventTarget(),
        0,
        &_hotKeyRef
    );
    NativeLog([NSString stringWithFormat:@"native hotkey register: %@ key=%u modifiers=%u status=%d", hotKey, keyCode, modifiers, status]);
}

- (BOOL)parseHotKey:(NSString *)hotKey keyCode:(UInt32 *)keyCode modifiers:(UInt32 *)modifiers {
    NSArray<NSString *> *parts = [hotKey componentsSeparatedByString:@"+"];
    BOOL foundKey = NO;
    UInt32 parsedModifiers = 0;
    UInt32 parsedKeyCode = 0;

    for (NSString *rawPart in parts) {
        NSString *part = [[rawPart stringByTrimmingCharactersInSet:[NSCharacterSet whitespaceAndNewlineCharacterSet]] lowercaseString];
        if ([part isEqualToString:@"<cmd>"] || [part isEqualToString:@"<command>"]) {
            parsedModifiers |= cmdKey;
        } else if ([part isEqualToString:@"<shift>"]) {
            parsedModifiers |= shiftKey;
        } else if ([part isEqualToString:@"<ctrl>"] || [part isEqualToString:@"<control>"]) {
            parsedModifiers |= controlKey;
        } else if ([part isEqualToString:@"<alt>"] || [part isEqualToString:@"<option>"]) {
            parsedModifiers |= optionKey;
        } else {
            NSString *key = part;
            if ([key hasPrefix:@"<"] && [key hasSuffix:@">"] && [key length] > 2) {
                key = [key substringWithRange:NSMakeRange(1, [key length] - 2)];
            }
            NSNumber *mapped = [self keyCodeForKey:key];
            if (!mapped) {
                return NO;
            }
            parsedKeyCode = [mapped unsignedIntValue];
            foundKey = YES;
        }
    }

    if (!foundKey) {
        return NO;
    }
    *keyCode = parsedKeyCode;
    *modifiers = parsedModifiers;
    return YES;
}

- (NSNumber *)keyCodeForKey:(NSString *)key {
    NSDictionary<NSString *, NSNumber *> *keyCodes = @{
        @"space": @49, @"tab": @48, @"enter": @36, @"return": @36,
        @"backspace": @51, @"esc": @53, @"escape": @53,
        @"left": @123, @"right": @124, @"down": @125, @"up": @126,
        @"a": @0, @"s": @1, @"d": @2, @"f": @3, @"h": @4, @"g": @5,
        @"z": @6, @"x": @7, @"c": @8, @"v": @9, @"b": @11,
        @"q": @12, @"w": @13, @"e": @14, @"r": @15, @"y": @16, @"t": @17,
        @"1": @18, @"2": @19, @"3": @20, @"4": @21, @"6": @22, @"5": @23,
        @"=": @24, @"9": @25, @"7": @26, @"-": @27, @"8": @28, @"0": @29,
        @"]": @30, @"o": @31, @"u": @32, @"[": @33, @"i": @34, @"p": @35,
        @"l": @37, @"j": @38, @"'": @39, @"k": @40, @";": @41,
        @"\\": @42, @",": @43, @"/": @44, @"n": @45, @"m": @46, @".": @47,
        @"`": @50, @"_": @94, @"underscore": @94,
    };
    return [keyCodes objectForKey:key];
}

- (void)startEngine {
    if (self.engineTask && self.engineTask.isRunning) {
        return;
    }
    NativeLog(@"startEngine");

    NSTask *task = [[NSTask alloc] init];
    task.executableURL = [NSURL fileURLWithPath:PythonPath()];
    task.arguments = @[ScriptPath(), @"--headless"];
    task.currentDirectoryURL = [NSURL fileURLWithPath:WorkDir()];
    task.standardOutput = [self appendHandleForPath:WorkPath(@"logs/native-engine.out.log")];
    task.standardError = [self appendHandleForPath:WorkPath(@"logs/native-engine.err.log")];
    task.terminationHandler = ^(NSTask *finishedTask) {
        NativeLog([NSString stringWithFormat:@"engine exited: status=%d", finishedTask.terminationStatus]);
    };
    self.engineTask = task;

    NSError *error = nil;
    if (![task launchAndReturnError:&error]) {
        NSLog(@"Voice Input Tool engine launch failed: %@", error);
        NativeLog([NSString stringWithFormat:@"engine launch failed: %@", error]);
    } else {
        NativeLog(@"engine launch complete");
    }
}

- (void)restartEngine {
    NativeLog(@"restartEngine");
    if (self.engineTask && self.engineTask.isRunning) {
        [self sendCommand:@"quit"];
        [self.engineTask terminate];
    }
    self.engineTask = nil;
    [self startEngine];
}

- (void)prepareOutputPolling {
    EnsureParentDirectory(OutputFilePath());
    if (![[NSFileManager defaultManager] fileExistsAtPath:OutputFilePath()]) {
        [[NSFileManager defaultManager] createFileAtPath:OutputFilePath() contents:nil attributes:nil];
    }

    NSDictionary *attrs = [[NSFileManager defaultManager] attributesOfItemAtPath:OutputFilePath() error:nil];
    self.outputOffset = [[attrs objectForKey:NSFileSize] unsignedLongLongValue];
    self.outputTimer = [NSTimer scheduledTimerWithTimeInterval:0.2
                                                        target:self
                                                      selector:@selector(pollOutputFile:)
                                                      userInfo:nil
                                                       repeats:YES];
    [self writePasteReadyHeartbeat];
    [self pollStatusFile];
    NativeLog([NSString stringWithFormat:@"output polling start: offset=%llu", self.outputOffset]);
}

- (void)pollOutputFile:(NSTimer *)timer {
    [self writePasteReadyHeartbeatIfNeeded];
    [self pollStatusFile];
    [self animateStatusIndicator];

    NSDictionary *attrs = [[NSFileManager defaultManager] attributesOfItemAtPath:OutputFilePath() error:nil];
    unsigned long long size = [[attrs objectForKey:NSFileSize] unsignedLongLongValue];
    if (size < self.outputOffset) {
        self.outputOffset = 0;
    }
    if (size == self.outputOffset) {
        return;
    }

    NSFileHandle *handle = [NSFileHandle fileHandleForReadingAtPath:OutputFilePath()];
    if (!handle) {
        return;
    }

    [handle seekToFileOffset:self.outputOffset];
    NSData *data = [handle readDataToEndOfFile];
    self.outputOffset += [data length];
    [handle closeFile];

    NSString *chunk = [[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding];
    NSArray<NSString *> *lines = [chunk componentsSeparatedByCharactersInSet:[NSCharacterSet newlineCharacterSet]];
    for (NSString *line in lines) {
        if ([[line stringByTrimmingCharactersInSet:[NSCharacterSet whitespaceAndNewlineCharacterSet]] length] == 0) {
            continue;
        }
        [self handleOutputLine:line];
    }
}

- (void)pollStatusFile {
    NSDictionary *attrs = [[NSFileManager defaultManager] attributesOfItemAtPath:StatusFilePath() error:nil];
    NSDate *modified = [attrs objectForKey:NSFileModificationDate];
    if (!modified) {
        return;
    }

    NSTimeInterval modifiedTime = [modified timeIntervalSince1970];
    if (modifiedTime <= self.lastStatusModified) {
        return;
    }
    self.lastStatusModified = modifiedTime;

    NSData *data = [NSData dataWithContentsOfFile:StatusFilePath()];
    if (!data) {
        return;
    }

    NSError *error = nil;
    id json = [NSJSONSerialization JSONObjectWithData:data options:0 error:&error];
    if (![json isKindOfClass:[NSDictionary class]]) {
        NativeLog([NSString stringWithFormat:@"status parse failed: %@", error]);
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
            self.statusItem.button.title = title;
        }
    }
    if ([recordTitle isKindOfClass:[NSString class]] && [recordTitle length] > 0) {
        self.toggleItem.title = recordTitle;
    }
    if ([llmTitle isKindOfClass:[NSString class]] && [llmTitle length] > 0) {
        self.llmItem.title = llmTitle;
    }
    NativeLog([NSString stringWithFormat:@"status updated: %@", status ?: @"unknown"]);
}

- (void)animateStatusIndicator {
    if (![self.currentStatus isEqualToString:@"hearing"]) {
        return;
    }
    NSArray<NSString *> *frames = @[@"•••", @"•  ", @"•• "];
    self.statusItem.button.title = [frames objectAtIndex:(self.statusAnimationFrame % [frames count])];
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
    EnsureParentDirectory(PasteReadyPath());
    NSString *content = [NSString stringWithFormat:@"%f\n", self.lastReadyHeartbeat];
    [content writeToFile:PasteReadyPath() atomically:YES encoding:NSUTF8StringEncoding error:nil];
}

- (void)handleOutputLine:(NSString *)line {
    NSData *data = [line dataUsingEncoding:NSUTF8StringEncoding];
    NSError *error = nil;
    id json = [NSJSONSerialization JSONObjectWithData:data options:0 error:&error];
    if (![json isKindOfClass:[NSDictionary class]]) {
        NativeLog([NSString stringWithFormat:@"output parse failed: %@", error]);
        return;
    }

    NSDictionary *payload = (NSDictionary *)json;
    NSString *text = [payload objectForKey:@"text"];
    NSNumber *pidNumber = [payload objectForKey:@"pid"];
    if (![text isKindOfClass:[NSString class]] || [text length] == 0) {
        NativeLog(@"output ignored: empty text");
        return;
    }

    pid_t pid = 0;
    if ([pidNumber respondsToSelector:@selector(intValue)]) {
        pid = [pidNumber intValue];
    }
    [self pasteText:text targetPid:pid];
}

- (void)pasteText:(NSString *)text targetPid:(pid_t)pid {
    NSPasteboard *pasteboard = [NSPasteboard generalPasteboard];
    [pasteboard clearContents];
    [pasteboard setString:text forType:NSPasteboardTypeString];
    NativeLog([NSString stringWithFormat:@"paste requested: pid=%d length=%lu", pid, (unsigned long)[text length]]);

    if (!AXIsProcessTrusted()) {
        NativeLog(@"paste skipped: accessibility not trusted; text copied to clipboard");
        [self requestAccessibilityIfNeeded];
        return;
    }

    if (pid > 0) {
        NSRunningApplication *target = [NSRunningApplication runningApplicationWithProcessIdentifier:pid];
        if (target) {
            [target activateWithOptions:NSApplicationActivateAllWindows];
        }
    }

    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(0.18 * NSEC_PER_SEC)), dispatch_get_main_queue(), ^{
        [self sendPasteShortcutToPid:pid];
    });
}

- (void)postEvent:(CGEventRef)event targetPid:(pid_t)pid {
    if (pid > 0) {
        CGEventPostToPid(pid, event);
    } else {
        CGEventPost(kCGHIDEventTap, event);
    }
}

- (void)postKey:(CGKeyCode)keycode down:(BOOL)down flags:(CGEventFlags)flags targetPid:(pid_t)pid {
    CGEventRef event = CGEventCreateKeyboardEvent(NULL, keycode, down);
    CGEventSetFlags(event, flags);
    [self postEvent:event targetPid:pid];
    CFRelease(event);
}

- (void)sendPasteShortcutToPid:(pid_t)pid {
    CGEventFlags flags = kCGEventFlagMaskCommand;
    [self postKey:55 down:YES flags:flags targetPid:pid];
    usleep(10000);
    [self postKey:9 down:YES flags:flags targetPid:pid];
    usleep(10000);
    [self postKey:9 down:NO flags:flags targetPid:pid];
    usleep(10000);
    [self postKey:55 down:NO flags:0 targetPid:pid];
    NativeLog([NSString stringWithFormat:@"paste shortcut sent: pid=%d", pid]);
}

- (void)sendTextDirectly:(NSString *)text targetPid:(pid_t)pid {
    NSUInteger length = [text length];
    for (NSUInteger index = 0; index < length; ) {
        NSRange range = [text rangeOfComposedCharacterSequenceAtIndex:index];
        NSUInteger charLength = range.length;
        UniChar *chars = malloc(sizeof(UniChar) * charLength);
        if (!chars) {
            NativeLog(@"direct text failed: malloc");
            [self sendPasteShortcutToPid:pid];
            return;
        }
        [text getCharacters:chars range:range];

        CGEventRef keyDown = CGEventCreateKeyboardEvent(NULL, 0, true);
        CGEventKeyboardSetUnicodeString(keyDown, charLength, chars);
        [self postEvent:keyDown targetPid:pid];
        CFRelease(keyDown);

        CGEventRef keyUp = CGEventCreateKeyboardEvent(NULL, 0, false);
        CGEventKeyboardSetUnicodeString(keyUp, charLength, chars);
        [self postEvent:keyUp targetPid:pid];
        CFRelease(keyUp);

        free(chars);
        index = NSMaxRange(range);
        usleep(3000);
    }
    NativeLog([NSString stringWithFormat:@"direct text sent: pid=%d length=%lu", pid, (unsigned long)[text length]]);
}

- (BOOL)sendCommand:(NSString *)command targetPid:(pid_t)targetPid {
    NSFileHandle *handle = [self appendHandleForPath:CommandFilePath()];
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
        NativeLog([NSString stringWithFormat:@"command json failed: %@", error]);
        line = command;
    }
    NSData *data = [[line stringByAppendingString:@"\n"] dataUsingEncoding:NSUTF8StringEncoding];
    [handle writeData:data];
    [handle closeFile];
    return YES;
}

- (void)toggleRecording:(id)sender {
    [self toggleRecordingForTargetPid:CurrentFrontmostApplicationPid()];
}

- (void)toggleRecordingForTargetPid:(pid_t)targetPid {
    NativeLog([NSString stringWithFormat:@"toggleRecording target_pid=%d", targetPid]);
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
    NativeLog(@"toggleLLM");
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
    NativeLog(@"checkAccessibility");
    [self requestAccessibilityIfNeeded];
    NSURL *url = [NSURL URLWithString:@"x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"];
    [[NSWorkspace sharedWorkspace] openURL:url];
}

- (void)openSettings:(id)sender {
    NativeLog(@"openSettings");
    NSTask *task = [[NSTask alloc] init];
    task.executableURL = [NSURL fileURLWithPath:PythonPath()];
    task.arguments = @[ScriptPath(), @"--settings"];
    task.currentDirectoryURL = [NSURL fileURLWithPath:WorkDir()];
    task.standardOutput = [self appendHandleForPath:WorkPath(@"logs/native-settings.out.log")];
    task.standardError = [self appendHandleForPath:WorkPath(@"logs/native-settings.err.log")];

    __weak AppDelegate *weakSelf = self;
    task.terminationHandler = ^(NSTask *finishedTask) {
        NativeLog([NSString stringWithFormat:@"settings exited: status=%d", finishedTask.terminationStatus]);
        dispatch_async(dispatch_get_main_queue(), ^{
            AppDelegate *strongSelf = weakSelf;
            if (strongSelf) {
                [strongSelf registerConfiguredHotKey];
                [strongSelf restartEngine];
            }
        });
    };

    NSError *error = nil;
    if (![task launchAndReturnError:&error]) {
        NativeLog([NSString stringWithFormat:@"settings launch failed: %@", error]);
    }
}

- (void)quit:(id)sender {
    NativeLog(@"quit");
    [self sendCommand:@"quit"];
    if (self.engineTask && self.engineTask.isRunning) {
        [self.engineTask terminate];
    }
    [NSApp terminate:nil];
}

- (void)applicationWillTerminate:(NSNotification *)notification {
    NativeLog(@"applicationWillTerminate");
    [self.outputTimer invalidate];
    [[NSFileManager defaultManager] removeItemAtPath:PasteReadyPath() error:nil];
    if (self.hotKeyRef) {
        UnregisterEventHotKey(self.hotKeyRef);
        self.hotKeyRef = NULL;
    }
    if (self.hotKeyHandlerRef) {
        RemoveEventHandler(self.hotKeyHandlerRef);
        self.hotKeyHandlerRef = NULL;
    }
    [self sendCommand:@"quit"];
    if (self.engineTask && self.engineTask.isRunning) {
        [self.engineTask terminate];
    }
}

@end

int main(int argc, const char * argv[]) {
    @autoreleasepool {
        NativeLog(@"main start");
        NSApplication *app = [NSApplication sharedApplication];
        NativeLog(@"sharedApplication complete");
        AppDelegate *delegate = [[AppDelegate alloc] init];
        app.delegate = delegate;
        [app run];
    }
    return 0;
}
