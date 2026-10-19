#import "VITCursorMicIndicator.h"
#import "VITNativeSupport.h"

@interface VITMicBadgeView : NSView
@end

@implementation VITMicBadgeView

- (BOOL)isOpaque { return NO; }

- (void)drawRect:(NSRect)dirtyRect {
    NSRect bounds = NSInsetRect(self.bounds, 1.0, 1.0);
    NSBezierPath *background = [NSBezierPath bezierPathWithRoundedRect:bounds xRadius:15.0 yRadius:15.0];
    [[NSColor colorWithCalibratedWhite:0.08 alpha:0.84] setFill];
    [background fill];
    [[NSColor colorWithCalibratedWhite:1.0 alpha:0.18] setStroke];
    [background setLineWidth:1.0];
    [background stroke];

    NSString *symbol = @"🎙";
    NSDictionary *attributes = @{
        NSFontAttributeName: [NSFont systemFontOfSize:18.0],
        NSForegroundColorAttributeName: [NSColor whiteColor],
    };
    NSSize size = [symbol sizeWithAttributes:attributes];
    NSRect rect = NSMakeRect(
        NSMidX(self.bounds) - size.width / 2.0,
        NSMidY(self.bounds) - size.height / 2.0 + 1.0,
        size.width,
        size.height
    );
    [symbol drawInRect:rect withAttributes:attributes];
}

@end

@interface VITCursorMicIndicator ()
@property(nonatomic, strong) NSPanel *panel;
@property(nonatomic, strong) NSTimer *timer;
@end

@implementation VITCursorMicIndicator

- (BOOL)shouldShowForStatus:(NSString *)status {
    return [status isEqualToString:@"starting"] ||
           [status isEqualToString:@"listening"] ||
           [status isEqualToString:@"hearing"];
}

- (NSPanel *)makePanel {
    NSRect frame = NSMakeRect(0, 0, 32, 32);
    NSPanel *panel = [[NSPanel alloc] initWithContentRect:frame
                                                styleMask:(NSWindowStyleMaskBorderless |
                                                           NSWindowStyleMaskNonactivatingPanel)
                                                  backing:NSBackingStoreBuffered
                                                    defer:NO];
    panel.opaque = NO;
    panel.backgroundColor = [NSColor clearColor];
    panel.hasShadow = YES;
    panel.ignoresMouseEvents = YES;
    panel.hidesOnDeactivate = NO;
    panel.canHide = NO;
    panel.becomesKeyOnlyIfNeeded = YES;
    panel.level = NSStatusWindowLevel;
    panel.collectionBehavior = NSWindowCollectionBehaviorCanJoinAllSpaces |
                               NSWindowCollectionBehaviorFullScreenAuxiliary |
                               NSWindowCollectionBehaviorTransient |
                               NSWindowCollectionBehaviorIgnoresCycle;
    panel.contentView = [[VITMicBadgeView alloc] initWithFrame:frame];
    return panel;
}

- (NSScreen *)screenContainingPoint:(NSPoint)point {
    for (NSScreen *screen in [NSScreen screens]) {
        if (NSPointInRect(point, screen.frame)) {
            return screen;
        }
    }
    return [NSScreen mainScreen];
}

- (void)positionPanel {
    if (!self.panel) {
        return;
    }
    NSPoint mouse = [NSEvent mouseLocation];
    NSSize badgeSize = self.panel.frame.size;
    NSPoint origin = NSMakePoint(mouse.x + 14.0, mouse.y - badgeSize.height - 10.0);
    NSScreen *screen = [self screenContainingPoint:mouse];
    if (screen) {
        NSRect visible = screen.visibleFrame;
        origin.x = MAX(NSMinX(visible), MIN(origin.x, NSMaxX(visible) - badgeSize.width));
        origin.y = MAX(NSMinY(visible), MIN(origin.y, NSMaxY(visible) - badgeSize.height));
    }
    [self.panel setFrameOrigin:origin];
}

- (void)show {
    if (!self.panel) {
        self.panel = [self makePanel];
    }
    [self positionPanel];
    [self.panel orderFrontRegardless];
    VITNativeLog([NSString stringWithFormat:@"cursor indicator show: visible=%@",
        self.panel.isVisible ? @"yes" : @"no"]);
    if (!self.timer) {
        self.timer = [NSTimer scheduledTimerWithTimeInterval:0.05
                                                     target:self
                                                   selector:@selector(timerFired:)
                                                   userInfo:nil
                                                    repeats:YES];
    }
}

- (void)hide {
    [self.timer invalidate];
    self.timer = nil;
    [self.panel orderOut:nil];
}

- (void)timerFired:(NSTimer *)timer { [self positionPanel]; }

- (void)updateForStatus:(NSString *)status {
    if ([self shouldShowForStatus:status]) {
        [self show];
    } else {
        [self hide];
    }
}

@end
