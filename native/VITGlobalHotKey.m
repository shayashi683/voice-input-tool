#import "VITGlobalHotKey.h"
#import "VITNativeSupport.h"
#import <ApplicationServices/ApplicationServices.h>
#import <Carbon/Carbon.h>

@interface VITGlobalHotKey ()
@property(nonatomic, copy) void (^handler)(void);
@property(nonatomic, assign) EventHotKeyRef hotKeyRef;
@property(nonatomic, assign) EventHandlerRef eventHandlerRef;
@property(nonatomic, strong) id globalFlagsMonitor;
@property(nonatomic, strong) id localFlagsMonitor;
@property(nonatomic, assign) CGKeyCode shiftKeyCode;
@property(nonatomic, assign) BOOL shiftKeyDown;
@end

static NSInteger VITShiftKeyCodeForHotKey(NSString *hotKey) {
    NSString *normalized = [[[hotKey stringByTrimmingCharactersInSet:
                              NSCharacterSet.whitespaceAndNewlineCharacterSet]
                             lowercaseString]
                            stringByTrimmingCharactersInSet:
                             [NSCharacterSet characterSetWithCharactersInString:@"<>"]];
    if ([normalized isEqualToString:@"left_shift"] ||
        [normalized isEqualToString:@"left shift"]) return 56;
    if ([normalized isEqualToString:@"right_shift"] ||
        [normalized isEqualToString:@"right shift"]) return 60;
    return -1;
}

static NSNumber *VITKeyCodeForKey(NSString *key) {
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

static BOOL VITParseHotKey(NSString *hotKey, UInt32 *keyCode, UInt32 *modifiers) {
    BOOL foundKey = NO;
    UInt32 parsedModifiers = 0;
    UInt32 parsedKeyCode = 0;
    for (NSString *rawPart in [hotKey componentsSeparatedByString:@"+"]) {
        NSString *part = [[rawPart stringByTrimmingCharactersInSet:
                           [NSCharacterSet whitespaceAndNewlineCharacterSet]] lowercaseString];
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
            NSNumber *mapped = VITKeyCodeForKey(key);
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

static OSStatus VITHotKeyPressed(
    EventHandlerCallRef nextHandler,
    EventRef event,
    void *userData
) {
    (void)nextHandler;
    (void)event;
    VITGlobalHotKey *controller = (__bridge VITGlobalHotKey *)userData;
    if (controller.handler) {
        controller.handler();
    }
    return noErr;
}

@implementation VITGlobalHotKey

- (void)removeShiftMonitors {
    if (self.globalFlagsMonitor) {
        [NSEvent removeMonitor:self.globalFlagsMonitor];
        self.globalFlagsMonitor = nil;
    }
    if (self.localFlagsMonitor) {
        [NSEvent removeMonitor:self.localFlagsMonitor];
        self.localFlagsMonitor = nil;
    }
    self.shiftKeyDown = NO;
}

- (void)unregisterCarbonHotKey {
    if (self.hotKeyRef) {
        UnregisterEventHotKey(self.hotKeyRef);
        self.hotKeyRef = NULL;
    }
}

- (void)handleShiftFlagsChanged:(NSEvent *)event {
    if (event.keyCode != self.shiftKeyCode) return;  // 56 is Left Shift; 60 is Right Shift.

    // Query the physical key so releasing one Shift is detected correctly even
    // when the other remains held and the aggregate Shift modifier stays active.
    BOOL isDown = CGEventSourceKeyState(kCGEventSourceStateCombinedSessionState, self.shiftKeyCode);
    if (isDown && !self.shiftKeyDown && self.handler) self.handler();
    self.shiftKeyDown = isDown;
}

- (BOOL)registerShiftHotKeyWithKeyCode:(CGKeyCode)keyCode {
    [self unregisterCarbonHotKey];
    [self removeShiftMonitors];
    self.shiftKeyCode = keyCode;

    NSDictionary *trustOptions = @{(__bridge id)kAXTrustedCheckOptionPrompt: @YES};
    BOOL accessibilityTrusted = AXIsProcessTrustedWithOptions((__bridge CFDictionaryRef)trustOptions);

    __weak VITGlobalHotKey *weakSelf = self;
    self.globalFlagsMonitor = [NSEvent addGlobalMonitorForEventsMatchingMask:NSEventMaskFlagsChanged
                                                                      handler:^(NSEvent *event) {
        [weakSelf handleShiftFlagsChanged:event];
    }];
    self.localFlagsMonitor = [NSEvent addLocalMonitorForEventsMatchingMask:NSEventMaskFlagsChanged
                                                                    handler:^NSEvent *(NSEvent *event) {
        [weakSelf handleShiftFlagsChanged:event];
        return event;
    }];
    BOOL registered = self.globalFlagsMonitor != nil && self.localFlagsMonitor != nil;
    NSString *side = keyCode == 56 ? @"Left Shift" : @"Right Shift";
    VITNativeLog([NSString stringWithFormat:@"native %@ hotkey register: %@ accessibility=%@",
                  side, registered ? @"ok" : @"failed", accessibilityTrusted ? @"yes" : @"no"]);
    if (!registered) [self removeShiftMonitors];
    return registered;
}

- (instancetype)initWithHandler:(void (^)(void))handler {
    self = [super init];
    if (self) {
        self.handler = handler;
    }
    return self;
}

- (BOOL)registerHotKey:(NSString *)hotKey {
    NSInteger shiftKeyCode = VITShiftKeyCodeForHotKey(hotKey);
    if (shiftKeyCode >= 0) {
        return [self registerShiftHotKeyWithKeyCode:(CGKeyCode)shiftKeyCode];
    }

    [self removeShiftMonitors];
    UInt32 keyCode = 0;
    UInt32 modifiers = 0;
    if (!VITParseHotKey(hotKey, &keyCode, &modifiers)) {
        VITNativeLog([NSString stringWithFormat:@"native hotkey parse failed: %@", hotKey]);
        return NO;
    }

    [self unregisterCarbonHotKey];
    if (!self.eventHandlerRef) {
        EventTypeSpec eventType = {kEventClassKeyboard, kEventHotKeyPressed};
        OSStatus status = InstallEventHandler(
            GetApplicationEventTarget(),
            VITHotKeyPressed,
            1,
            &eventType,
            (__bridge void *)self,
            &_eventHandlerRef
        );
        if (status != noErr) {
            VITNativeLog([NSString stringWithFormat:@"native hotkey handler failed: %d", status]);
            return NO;
        }
    }

    EventHotKeyID hotKeyId = {'VITK', 1};
    OSStatus status = RegisterEventHotKey(
        keyCode,
        modifiers,
        hotKeyId,
        GetApplicationEventTarget(),
        0,
        &_hotKeyRef
    );
    VITNativeLog([NSString stringWithFormat:
        @"native hotkey register: %@ key=%u modifiers=%u status=%d",
        hotKey, keyCode, modifiers, status]);
    return status == noErr;
}

- (void)invalidate {
    [self unregisterCarbonHotKey];
    [self removeShiftMonitors];
    if (self.eventHandlerRef) {
        RemoveEventHandler(self.eventHandlerRef);
        self.eventHandlerRef = NULL;
    }
}

- (void)dealloc { [self invalidate]; }

@end
