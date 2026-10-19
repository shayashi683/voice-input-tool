#import <Cocoa/Cocoa.h>

FOUNDATION_EXPORT BOOL VITSetupGuideNeedsPresentation(NSString *workDirectory);
FOUNDATION_EXPORT BOOL VITMarkSetupGuideCompleted(NSString *workDirectory, NSError **error);
FOUNDATION_EXPORT NSString *VITSetupGuideHotKeyDisplay(NSString *workDirectory);

@interface VITSetupGuideController : NSWindowController <NSWindowDelegate>
- (instancetype)initWithWorkDirectory:(NSString *)workDirectory
                     settingsHandler:(void (^)(void))settingsHandler
                accessibilityHandler:(void (^)(void))accessibilityHandler;
- (void)showGuide;
- (void)refreshConfiguredHotKey;
@end
