#import "VITSetupGuideController.h"

static NSInteger const VITSetupGuideVersion = 1;

static NSString *VITSetupGuideMarkerPath(NSString *directory) {
    return [directory stringByAppendingPathComponent:@"setup-guide.json"];
}

static NSDictionary *VITReadGuideDictionary(NSString *path) {
    NSData *data = [NSData dataWithContentsOfFile:path];
    if (!data) return @{};
    id value = [NSJSONSerialization JSONObjectWithData:data options:0 error:nil];
    return [value isKindOfClass:NSDictionary.class] ? value : @{};
}

BOOL VITSetupGuideNeedsPresentation(NSString *workDirectory) {
    NSDictionary *marker = VITReadGuideDictionary(VITSetupGuideMarkerPath(workDirectory));
    NSNumber *version = marker[@"version"];
    NSNumber *completed = marker[@"completed"];
    return !([version isKindOfClass:NSNumber.class] && version.integerValue >= VITSetupGuideVersion &&
             [completed isKindOfClass:NSNumber.class] && completed.boolValue);
}

BOOL VITMarkSetupGuideCompleted(NSString *workDirectory, NSError **error) {
    NSFileManager *files = NSFileManager.defaultManager;
    if (![files createDirectoryAtPath:workDirectory withIntermediateDirectories:YES
                          attributes:@{NSFilePosixPermissions: @0700} error:error]) return NO;
    NSDictionary *marker = @{
        @"version": @(VITSetupGuideVersion),
        @"completed": @YES,
        @"completed_at": @([NSDate.date timeIntervalSince1970]),
    };
    NSData *data = [NSJSONSerialization dataWithJSONObject:marker options:NSJSONWritingPrettyPrinted error:error];
    NSString *path = VITSetupGuideMarkerPath(workDirectory);
    if (!data || ![data writeToFile:path options:NSDataWritingAtomic error:error]) return NO;
    if (![files setAttributes:@{NSFilePosixPermissions: @0600} ofItemAtPath:path error:error]) {
        [files removeItemAtPath:path error:nil];
        return NO;
    }
    return YES;
}

NSString *VITSetupGuideHotKeyDisplay(NSString *workDirectory) {
    NSDictionary *config = VITReadGuideDictionary([workDirectory stringByAppendingPathComponent:@"config.json"]);
    NSString *hotKey = config[@"hotkey_record"];
    if (![hotKey isKindOfClass:NSString.class] ||
        [hotKey stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet].length == 0) {
        hotKey = @"<ctrl>+<shift>+<space>";
    }
    NSDictionary *names = @{
        @"left_shift": @"Left Shift",
        @"right_shift": @"Right Shift",
        @"ctrl": @"Ctrl", @"control": @"Ctrl", @"shift": @"Shift", @"cmd": @"Cmd", @"command": @"Cmd",
        @"alt": @"Option", @"option": @"Option", @"space": @"Space", @"tab": @"Tab",
        @"enter": @"Enter", @"return": @"Enter", @"backspace": @"Backspace", @"esc": @"Esc", @"escape": @"Esc",
        @"left": @"←", @"right": @"→", @"up": @"↑", @"down": @"↓",
    };
    NSMutableArray<NSString *> *parts = [NSMutableArray array];
    for (NSString *rawPart in [hotKey componentsSeparatedByString:@"+"]) {
        NSString *part = [[[rawPart stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet]
                          stringByTrimmingCharactersInSet:[NSCharacterSet characterSetWithCharactersInString:@"<>"]] lowercaseString];
        if (part.length) [parts addObject:names[part] ?: part.uppercaseString];
    }
    return parts.count ? [parts componentsJoinedByString:@" + "] : @"Ctrl + Shift + Space";
}

@interface VITGuideBackgroundView : NSView
@end
@implementation VITGuideBackgroundView
- (void)drawRect:(NSRect)dirtyRect {
    [NSColor.windowBackgroundColor setFill];
    NSRectFill(dirtyRect);
}
@end

@interface VITGuideCardView : NSView
@end
@implementation VITGuideCardView
- (void)drawRect:(NSRect)dirtyRect {
    (void)dirtyRect;
    NSBezierPath *path = [NSBezierPath bezierPathWithRoundedRect:NSInsetRect(self.bounds, 0.5, 0.5) xRadius:12 yRadius:12];
    [NSColor.controlBackgroundColor setFill];
    [path fill];
    [[NSColor.separatorColor colorWithAlphaComponent:0.5] setStroke];
    [path stroke];
}
@end

static NSTextField *VITGuideLabel(NSString *text, NSRect frame, CGFloat size, NSFontWeight weight, NSColor *color) {
    NSTextField *label = [NSTextField wrappingLabelWithString:text];
    label.frame = frame;
    label.font = [NSFont systemFontOfSize:size weight:weight];
    label.textColor = color;
    label.selectable = YES;
    label.maximumNumberOfLines = 0;
    return label;
}

@interface VITSetupGuideController ()
@property(nonatomic, copy) NSString *workDirectory;
@property(nonatomic, copy) void (^settingsHandler)(void);
@property(nonatomic, copy) void (^accessibilityHandler)(void);
@property(nonatomic, strong) NSTextField *hotKeyLabel;
@property(nonatomic, strong) NSScrollView *guideScrollView;
@end

@implementation VITSetupGuideController

- (instancetype)initWithWorkDirectory:(NSString *)workDirectory
                     settingsHandler:(void (^)(void))settingsHandler
                accessibilityHandler:(void (^)(void))accessibilityHandler {
    NSWindow *window = [[NSWindow alloc] initWithContentRect:NSMakeRect(0, 0, 660, 680)
                                                styleMask:NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
                                                  backing:NSBackingStoreBuffered defer:NO];
    self = [super initWithWindow:window];
    if (self) {
        self.workDirectory = workDirectory;
        self.settingsHandler = settingsHandler;
        self.accessibilityHandler = accessibilityHandler;
        window.title = @"Voice Input Tool — はじめての設定";
        window.releasedWhenClosed = NO;
        window.delegate = self;
        [self buildGuide];
        NSScreen *screen = NSScreen.mainScreen;
        if (screen) [self fitToVisibleFrame:screen.visibleFrame];
        [window center];
    }
    return self;
}

- (NSButton *)button:(NSString *)title frame:(NSRect)frame action:(SEL)action {
    NSButton *button = [NSButton buttonWithTitle:title target:self action:action];
    button.bezelStyle = NSBezelStyleRounded;
    button.frame = frame;
    button.font = [NSFont systemFontOfSize:12 weight:NSFontWeightMedium];
    return button;
}

- (NSView *)card:(NSInteger)number title:(NSString *)title frame:(NSRect)frame {
    NSView *card = [[VITGuideCardView alloc] initWithFrame:frame];
    [self.window.contentView addSubview:card];
    NSTextField *badge = VITGuideLabel([NSString stringWithFormat:@"%ld", (long)number],
                                      NSMakeRect(20, frame.size.height - 48, 30, 28), 21,
                                      NSFontWeightSemibold, NSColor.controlAccentColor);
    badge.selectable = NO;
    [card addSubview:badge];
    [card addSubview:VITGuideLabel(title, NSMakeRect(64, frame.size.height - 43, 510, 25),
                                  16, NSFontWeightSemibold, NSColor.labelColor)];
    return card;
}

- (void)buildGuide {
    NSView *content = [[VITGuideBackgroundView alloc] initWithFrame:NSMakeRect(0, 0, 660, 680)];
    self.window.contentView = content;
    [content addSubview:VITGuideLabel(@"声で入力する準備をしましょう", NSMakeRect(28, 621, 604, 34),
                                     24, NSFontWeightSemibold, NSColor.labelColor)];
    [content addSubview:VITGuideLabel(@"GPT-Transcribeでは録音音声をOpenAIへ送信します。", NSMakeRect(29, 593, 600, 22),
                                     13, NSFontWeightRegular, NSColor.secondaryLabelColor)];

    NSView *first = [self card:1 title:@"共有のAPIキーを設定する" frame:NSMakeRect(28, 395, 604, 184)];
    [first addSubview:VITGuideLabel(@"1Passwordで「noko_shared」保管庫を開き、\n「VoiceInputTool - OpenAI API」のAPIキーをコピーします。",
                                   NSMakeRect(64, 86, 516, 47), 13, NSFontWeightRegular, NSColor.labelColor)];
    [first addSubview:VITGuideLabel(@"アプリの設定で「OpenAI GPT-Transcribe」を選び、\n「OpenAI API Key」に貼り付けて保存します。",
                                   NSMakeRect(64, 42, 516, 43), 13, NSFontWeightRegular, NSColor.labelColor)];
    [first addSubview:[self button:@"1Passwordを開く" frame:NSMakeRect(58, 5, 164, 30) action:@selector(openOnePassword:)]];
    [first addSubview:[self button:@"アプリの設定を開く" frame:NSMakeRect(232, 5, 180, 30) action:@selector(openAppSettings:)]];

    NSView *second = [self card:2 title:@"Macの権限を許可する" frame:NSMakeRect(28, 239, 604, 144)];
    [second addSubview:VITGuideLabel(@"システム設定 → プライバシーとセキュリティで、\n「VoiceInputTool」のマイクとアクセシビリティをオンにします。",
                                    NSMakeRect(64, 49, 516, 46), 13, NSFontWeightRegular, NSColor.labelColor)];
    [second addSubview:[self button:@"マイクの設定" frame:NSMakeRect(58, 12, 142, 30) action:@selector(openMicrophoneSettings:)]];
    [second addSubview:[self button:@"アクセシビリティの設定" frame:NSMakeRect(211, 12, 206, 30) action:@selector(openAccessibilitySettings:)]];

    NSView *third = [self card:3 title:@"短い文章で試してみる" frame:NSMakeRect(28, 71, 604, 156)];
    self.hotKeyLabel = VITGuideLabel(@"", NSMakeRect(64, 82, 516, 25), 15, NSFontWeightSemibold, NSColor.controlAccentColor);
    [third addSubview:self.hotKeyLabel];
    [third addSubview:VITGuideLabel(@"「新規書類」を開き、入力欄をクリックしてショートカットを押す。\n「今日は音声入力を試しています」と話し、同じキーで停止。\n入力欄に文字が入れば準備完了です。",
                                   NSMakeRect(64, 34, 516, 47), 12, NSFontWeightRegular, NSColor.labelColor)];
    [third addSubview:[self button:@"テキストエディットを開く" frame:NSMakeRect(58, 2, 212, 29) action:@selector(openTextEdit:)]];

    [content addSubview:VITGuideLabel(@"完了すると次回から自動表示しません。\nメニューバーからいつでも開けます。",
                                     NSMakeRect(29, 13, 320, 39), 11, NSFontWeightRegular, NSColor.secondaryLabelColor)];
    NSButton *later = [self button:@"あとで" frame:NSMakeRect(382, 17, 86, 32) action:@selector(dismissGuide:)];
    later.keyEquivalent = @"\e";
    [content addSubview:later];
    NSButton *done = [self button:@"案内を完了" frame:NSMakeRect(476, 17, 158, 32) action:@selector(completeGuide:)];
    [content addSubview:done];
    [self refreshConfiguredHotKey];
}

- (void)refreshConfiguredHotKey {
    self.hotKeyLabel.stringValue = [NSString stringWithFormat:@"録音の開始・停止： %@", VITSetupGuideHotKeyDisplay(self.workDirectory)];
}

- (void)fitToVisibleFrame:(NSRect)visibleFrame {
    // Smaller display settings still keep "later" and "done" on screen.
    CGFloat availableHeight = visibleFrame.size.height - 20;
    if (self.window.frame.size.height <= availableHeight) return;
    CGFloat contentHeight = [NSWindow contentRectForFrameRect:NSMakeRect(0, 0, 660, availableHeight)
                                                  styleMask:self.window.styleMask].size.height;
    contentHeight = MAX(400, contentHeight);
    if (self.guideScrollView) {
        [self.window setContentSize:NSMakeSize(660, contentHeight)];
        self.guideScrollView.frame = NSMakeRect(0, 70, 660, contentHeight - 70);
        return;
    }
    NSView *document = self.window.contentView;
    NSView *root = [[VITGuideBackgroundView alloc] initWithFrame:NSMakeRect(0, 0, 660, contentHeight)];
    for (NSView *view in [document.subviews copy]) {
        if (view.frame.origin.y < 70) {
            [view removeFromSuperview];
            [root addSubview:view];
        } else {
            NSRect frame = view.frame;
            frame.origin.y -= 70;
            view.frame = frame;
        }
    }
    document.frame = NSMakeRect(0, 0, 660, 610);
    NSScrollView *scroll = [[NSScrollView alloc] initWithFrame:NSMakeRect(0, 70, 660, contentHeight - 70)];
    scroll.hasVerticalScroller = YES;
    scroll.autohidesScrollers = YES;
    scroll.drawsBackground = NO;
    scroll.documentView = document;
    self.guideScrollView = scroll;
    [root addSubview:scroll];
    self.window.contentView = root;
    [self.window setContentSize:NSMakeSize(660, contentHeight)];
    [scroll.contentView scrollToPoint:NSMakePoint(0, 610 - scroll.contentView.bounds.size.height)];
    [scroll reflectScrolledClipView:scroll.contentView];
}

- (void)showGuide {
    [self refreshConfiguredHotKey];
    [self showWindow:nil];
    [self.window makeKeyAndOrderFront:nil];
    [NSApp activateIgnoringOtherApps:YES];
}

- (void)windowDidBecomeKey:(NSNotification *)notification {
    (void)notification;
    [self refreshConfiguredHotKey];
}

- (void)showError:(NSString *)message detail:(NSString *)detail {
    NSAlert *alert = [[NSAlert alloc] init];
    alert.messageText = message;
    alert.informativeText = detail;
    [alert beginSheetModalForWindow:self.window completionHandler:nil];
}

- (void)openApplication:(NSString *)bundleIdentifier displayName:(NSString *)displayName {
    NSURL *url = [NSWorkspace.sharedWorkspace URLForApplicationWithBundleIdentifier:bundleIdentifier];
    if (!url) {
        [self showError:[displayName stringByAppendingString:@"を開けませんでした"]
                 detail:@"アプリがインストールされているか確認してください。"];
        return;
    }
    __weak VITSetupGuideController *weakSelf = self;
    [NSWorkspace.sharedWorkspace openApplicationAtURL:url configuration:NSWorkspaceOpenConfiguration.configuration
                                   completionHandler:^(NSRunningApplication *app, NSError *error) {
        (void)app;
        if (error) dispatch_async(dispatch_get_main_queue(), ^{
            [weakSelf showError:[displayName stringByAppendingString:@"を開けませんでした"]
                         detail:@"LaunchpadまたはFinderから開いてください。"];
        });
    }];
}

- (void)openOnePassword:(id)sender {
    (void)sender;
    [self openApplication:@"com.1password.1password" displayName:@"1Password"];
}

- (void)openAppSettings:(id)sender {
    (void)sender;
    if (self.settingsHandler) self.settingsHandler();
}

- (void)openMicrophoneSettings:(id)sender {
    (void)sender;
    NSArray<NSString *> *urls = @[
        @"x-apple.systempreferences:com.apple.settings.PrivacySecurity.extension?Privacy_Microphone",
        @"x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone",
    ];
    for (NSString *value in urls) {
        if ([NSWorkspace.sharedWorkspace openURL:[NSURL URLWithString:value]]) return;
    }
    [self showError:@"システム設定を開けませんでした" detail:@"システム設定 → プライバシーとセキュリティ → マイクを開いてください。"];
}

- (void)openAccessibilitySettings:(id)sender {
    (void)sender;
    if (self.accessibilityHandler) self.accessibilityHandler();
}

- (void)openTextEdit:(id)sender {
    (void)sender;
    [self openApplication:@"com.apple.TextEdit" displayName:@"テキストエディット"];
}

- (void)dismissGuide:(id)sender {
    (void)sender;
    // Closing, Escape and "later" leave the first-run marker untouched.
    [self close];
}

- (void)completeGuide:(id)sender {
    (void)sender;
    NSError *error = nil;
    if (!VITMarkSetupGuideCompleted(self.workDirectory, &error)) {
        [self showError:@"完了状態を保存できませんでした" detail:@"アプリを再起動したあと、もう一度「案内を完了」を押してください。"];
        return;
    }
    [self close];
}

@end
