#import "VITTextPasteController.h"
#import "VITNativeSupport.h"
#import <ApplicationServices/ApplicationServices.h>
#include <unistd.h>

@interface VITTextPasteController ()
@property(nonatomic, copy) void (^statusTitleHandler)(NSString *title);
@property(nonatomic, assign) BOOL accessibilityWarningShown;
@property(nonatomic, strong) NSMutableArray<NSDictionary *> *requests;
@property(nonatomic, assign) BOOL busy;
@end

@implementation VITTextPasteController

- (instancetype)initWithStatusTitleHandler:(void (^)(NSString *title))handler {
    self = [super init];
    if (self) {
        self.statusTitleHandler = handler;
        self.requests = [NSMutableArray array];
    }
    return self;
}

- (void)setStatusTitle:(NSString *)title {
    if (self.statusTitleHandler) {
        self.statusTitleHandler(title);
    }
}

- (void)pasteText:(NSString *)text targetPid:(pid_t)pid expiresAt:(NSTimeInterval)deadline completion:(void (^)(NSString *))completion {
    [self.requests addObject:@{@"text": text, @"pid": @(pid), @"deadline": @(deadline), @"completion": [completion copy]}];
    [self processNext];
}

- (void)finishRequest:(NSDictionary *)request status:(NSString *)status {
    void (^completion)(NSString *) = request[@"completion"];
    completion(status);
    // Allow the receiving application to consume Cmd+V before changing clipboard.
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(0.3 * NSEC_PER_SEC)),
                   dispatch_get_main_queue(), ^{
        self.busy = NO;
        [self processNext];
    });
}

- (void)processNext {
    if (self.busy || self.requests.count == 0) return;
    self.busy = YES;
    NSDictionary *request = self.requests.firstObject;
    [self.requests removeObjectAtIndex:0];
    NSString *text = request[@"text"];
    pid_t pid = [request[@"pid"] intValue];
    NSTimeInterval deadline = [request[@"deadline"] doubleValue];
    if (deadline > 0 && [[NSDate date] timeIntervalSince1970] > deadline) {
        [self finishRequest:request status:@"failed"];
        return;
    }
    NSPasteboard *pasteboard = [NSPasteboard generalPasteboard];
    [pasteboard clearContents];
    if (![pasteboard setString:text forType:NSPasteboardTypeString]) {
        [self finishRequest:request status:@"failed"];
        return;
    }
    NSInteger clipboardVersion = pasteboard.changeCount;
    VITNativeLog([NSString stringWithFormat:@"paste requested: pid=%d length=%lu", pid, (unsigned long)text.length]);
    if (!AXIsProcessTrusted()) {
        [self finishRequest:request status:@"copied"];
        [self showAccessibilityRequiredAlert];
        return;
    }
    if (pid > 0) {
        NSRunningApplication *target = [NSRunningApplication runningApplicationWithProcessIdentifier:pid];
        if (!target || target.terminated) {
            [self finishRequest:request status:@"copied"];
            return;
        }
        [target activateWithOptions:(NSApplicationActivateIgnoringOtherApps | NSApplicationActivateAllWindows)];
    }
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(0.25 * NSEC_PER_SEC)),
                   dispatch_get_main_queue(), ^{
        if (pasteboard.changeCount != clipboardVersion) {
            [self finishRequest:request status:@"failed"];
            return;
        }
        BOOL sent = [self sendPasteShortcutAfterActivatingPid:pid];
        [self finishRequest:request status:sent ? @"paste_sent" : @"copied"];
    });
}

- (void)postKey:(CGKeyCode)keycode down:(BOOL)down flags:(CGEventFlags)flags {
    CGEventRef event = CGEventCreateKeyboardEvent(NULL, keycode, down);
    CGEventSetFlags(event, flags);
    CGEventPost(kCGHIDEventTap, event);
    CFRelease(event);
}

- (BOOL)waitForFrontmostPid:(pid_t)pid timeout:(NSTimeInterval)timeout {
    if (pid <= 0) {
        return YES;
    }
    NSDate *deadline = [NSDate dateWithTimeIntervalSinceNow:timeout];
    while ([[NSDate date] compare:deadline] == NSOrderedAscending) {
        NSRunningApplication *frontmost = [[NSWorkspace sharedWorkspace] frontmostApplication];
        if (frontmost && [frontmost processIdentifier] == pid) {
            return YES;
        }
        usleep(20000);
    }
    return NO;
}

- (BOOL)sendPasteShortcutAfterActivatingPid:(pid_t)pid {
    BOOL targetIsFrontmost = [self waitForFrontmostPid:pid timeout:0.45];
    if (pid > 0 && !targetIsFrontmost) {
        VITNativeLog([NSString stringWithFormat:@"paste target not frontmost after activation: pid=%d", pid]);
        return NO;
    }

    CGEventFlags flags = kCGEventFlagMaskCommand;
    [self postKey:55 down:YES flags:flags];
    usleep(12000);
    [self postKey:9 down:YES flags:flags];
    usleep(12000);
    [self postKey:9 down:NO flags:flags];
    usleep(12000);
    [self postKey:55 down:NO flags:0];
    VITNativeLog([NSString stringWithFormat:
        @"paste shortcut sent to frontmost app: requested_pid=%d frontmost=%@",
        pid, targetIsFrontmost ? @"yes" : @"no"]);
    return YES;
}

- (void)showAccessibilityRequiredAlert {
    if (self.accessibilityWarningShown) {
        return;
    }
    self.accessibilityWarningShown = YES;
    [self setStatusTitle:@"⚠︎"];

    dispatch_async(dispatch_get_main_queue(), ^{
        NSAlert *alert = [[NSAlert alloc] init];
        alert.messageText = @"テキストボックスへ入力するには許可が必要です";
        alert.informativeText = @"認識した文字はクリップボードにコピー済みです。システム設定の「プライバシーとセキュリティ」>「アクセシビリティ」でVoiceInputToolを許可すると、次回からカーソル位置へ自動入力できます。";
        [alert addButtonWithTitle:@"設定を開く"];
        [alert addButtonWithTitle:@"あとで"];
        if ([alert runModal] == NSAlertFirstButtonReturn) {
            VITOpenAccessibilitySettings();
        }
    });
}

@end
