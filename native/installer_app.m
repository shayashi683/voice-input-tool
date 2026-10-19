#import <Cocoa/Cocoa.h>

static NSString *ShellQuote(NSString *value) {
    NSString *escaped = [value stringByReplacingOccurrencesOfString:@"'" withString:@"'\\''"];
    return [NSString stringWithFormat:@"'%@'", escaped];
}

@interface InstallerDelegate : NSObject <NSApplicationDelegate>
@property(nonatomic, strong) NSWindow *window;
@property(nonatomic, strong) NSTextField *statusLabel;
@property(nonatomic, strong) NSTextView *logView;
@property(nonatomic, strong) NSProgressIndicator *spinner;
@property(nonatomic, strong) NSButton *openButton;
@property(nonatomic, strong) NSButton *quitButton;
@property(nonatomic, strong) NSTask *task;
@property(nonatomic, copy) NSString *installedAppPath;
@end

@implementation InstallerDelegate

- (void)applicationDidFinishLaunching:(NSNotification *)notification {
    (void)notification;
    [self buildWindow];
    [self appendLog:@"Voice Input Tool のセットアップを開始します。\n"];
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(0.4 * NSEC_PER_SEC)), dispatch_get_main_queue(), ^{
        [self startInstall];
    });
}

- (BOOL)applicationShouldTerminateAfterLastWindowClosed:(NSApplication *)sender {
    (void)sender;
    return YES;
}

- (void)buildWindow {
    NSRect frame = NSMakeRect(0, 0, 620, 420);
    self.window = [[NSWindow alloc] initWithContentRect:frame
                                              styleMask:(NSWindowStyleMaskTitled |
                                                         NSWindowStyleMaskClosable |
                                                         NSWindowStyleMaskMiniaturizable)
                                                backing:NSBackingStoreBuffered
                                                  defer:NO];
    self.window.title = @"Voice Input Tool Installer";
    [self.window center];

    NSView *content = self.window.contentView;

    NSTextField *title = [NSTextField labelWithString:@"Voice Input Tool をインストールしています"];
    title.font = [NSFont boldSystemFontOfSize:20];
    title.frame = NSMakeRect(28, 362, 560, 28);
    [content addSubview:title];

    self.statusLabel = [NSTextField labelWithString:@"環境構築を準備しています..."];
    self.statusLabel.font = [NSFont systemFontOfSize:13];
    self.statusLabel.textColor = [NSColor secondaryLabelColor];
    self.statusLabel.frame = NSMakeRect(28, 336, 500, 20);
    [content addSubview:self.statusLabel];

    self.spinner = [[NSProgressIndicator alloc] initWithFrame:NSMakeRect(558, 334, 24, 24)];
    self.spinner.style = NSProgressIndicatorStyleSpinning;
    self.spinner.indeterminate = YES;
    [self.spinner startAnimation:nil];
    [content addSubview:self.spinner];

    NSScrollView *scroll = [[NSScrollView alloc] initWithFrame:NSMakeRect(28, 74, 564, 246)];
    scroll.borderType = NSBezelBorder;
    scroll.hasVerticalScroller = YES;
    scroll.autohidesScrollers = YES;
    self.logView = [[NSTextView alloc] initWithFrame:NSMakeRect(0, 0, 564, 246)];
    self.logView.editable = NO;
    self.logView.font = [NSFont monospacedSystemFontOfSize:12 weight:NSFontWeightRegular];
    self.logView.textColor = [NSColor labelColor];
    scroll.documentView = self.logView;
    [content addSubview:scroll];

    self.openButton = [NSButton buttonWithTitle:@"アプリを開く" target:self action:@selector(openInstalledApp:)];
    self.openButton.frame = NSMakeRect(370, 24, 106, 32);
    self.openButton.enabled = NO;
    [content addSubview:self.openButton];

    self.quitButton = [NSButton buttonWithTitle:@"閉じる" target:self action:@selector(quit:)];
    self.quitButton.frame = NSMakeRect(486, 24, 106, 32);
    [content addSubview:self.quitButton];

    [self.window makeKeyAndOrderFront:nil];
    [NSApp activateIgnoringOtherApps:YES];
}

- (void)startInstall {
    NSString *payloadPath = [[[NSBundle mainBundle] resourcePath] stringByAppendingPathComponent:@"payload"];
    BOOL isDir = NO;
    if (![[NSFileManager defaultManager] fileExistsAtPath:payloadPath isDirectory:&isDir] || !isDir) {
        [self failWithMessage:@"インストール用ファイルが見つかりませんでした。"];
        return;
    }

    NSString *home = NSHomeDirectory();
    NSString *installRoot = [home stringByAppendingPathComponent:@"Library/Application Support/VoiceInputTool"];
    self.installedAppPath = [home stringByAppendingPathComponent:@"Applications/VoiceInputTool.app"];

    self.statusLabel.stringValue = @"必要ファイルを配置し、依存関係をインストールしています...";
    [self appendLog:[NSString stringWithFormat:@"インストール先: %@\n", installRoot]];

    NSString *script = [NSString stringWithFormat:
        @"set -euo pipefail\n"
         "PAYLOAD=%@\n"
         "INSTALL_ROOT=%@\n"
         "APP_BUNDLE=%@\n"
         "/bin/mkdir -p \"$(/usr/bin/dirname \"$INSTALL_ROOT\")\"\n"
         "/usr/bin/ditto \"$PAYLOAD\" \"$INSTALL_ROOT\"\n"
         "cd \"$INSTALL_ROOT\"\n"
         "/bin/chmod +x start.sh scripts/*.sh 2>/dev/null || true\n"
         "APP_BUNDLE_PATH=\"$APP_BUNDLE\" ./scripts/install_mac_app.sh\n",
         ShellQuote(payloadPath),
         ShellQuote(installRoot),
         ShellQuote(self.installedAppPath)];

    NSPipe *pipe = [NSPipe pipe];
    self.task = [[NSTask alloc] init];
    self.task.launchPath = @"/bin/bash";
    self.task.arguments = @[@"-c", script];
    self.task.standardOutput = pipe;
    self.task.standardError = pipe;

    __weak typeof(self) weakSelf = self;
    pipe.fileHandleForReading.readabilityHandler = ^(NSFileHandle *handle) {
        NSData *data = [handle availableData];
        if (data.length == 0) {
            handle.readabilityHandler = nil;
            return;
        }
        NSString *text = [[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding];
        if (!text) {
            text = [[NSString alloc] initWithData:data encoding:NSMacOSRomanStringEncoding];
        }
        if (text.length > 0) {
            dispatch_async(dispatch_get_main_queue(), ^{
                [weakSelf appendLog:text];
            });
        }
    };

    self.task.terminationHandler = ^(NSTask *task) {
        dispatch_async(dispatch_get_main_queue(), ^{
            [weakSelf installDidFinishWithStatus:task.terminationStatus];
        });
    };

    @try {
        [self.task launch];
    } @catch (NSException *exception) {
        [self failWithMessage:[NSString stringWithFormat:@"インストーラーを起動できませんでした: %@", exception.reason]];
    }
}

- (void)installDidFinishWithStatus:(int)status {
    self.task = nil;
    [self.spinner stopAnimation:nil];
    if (status == 0) {
        self.statusLabel.stringValue = @"セットアップが完了しました。";
        self.openButton.enabled = YES;
        [self appendLog:@"\nセットアップが完了しました。VoiceInputTool.app を開きます。\n"];
        [self openInstalledApp:nil];
        return;
    }

    self.statusLabel.stringValue = @"セットアップに失敗しました。ログを確認してください。";
    [self appendLog:[NSString stringWithFormat:@"\nセットアップに失敗しました。終了コード: %d\n", status]];
}

- (void)failWithMessage:(NSString *)message {
    [self.spinner stopAnimation:nil];
    self.statusLabel.stringValue = message;
    [self appendLog:[NSString stringWithFormat:@"%@\n", message]];
}

- (void)appendLog:(NSString *)text {
    if (!text) {
        return;
    }
    NSAttributedString *entry = [[NSAttributedString alloc] initWithString:text];
    [[self.logView textStorage] appendAttributedString:entry];
    [self.logView scrollRangeToVisible:NSMakeRange(self.logView.string.length, 0)];
}

- (void)openInstalledApp:(id)sender {
    (void)sender;
    if (!self.installedAppPath) {
        return;
    }
    NSURL *url = [NSURL fileURLWithPath:self.installedAppPath];
    if (![[NSWorkspace sharedWorkspace] openURL:url]) {
        [self appendLog:@"VoiceInputTool.app を自動で開けませんでした。~/Applications から開いてください。\n"];
    }
}

- (void)quit:(id)sender {
    (void)sender;
    [NSApp terminate:nil];
}

@end

int main(int argc, const char *argv[]) {
    (void)argc;
    (void)argv;
    @autoreleasepool {
        NSApplication *app = [NSApplication sharedApplication];
        InstallerDelegate *delegate = [[InstallerDelegate alloc] init];
        app.delegate = delegate;
        [app run];
    }
    return 0;
}
