#import <Cocoa/Cocoa.h>

@interface VITCursorMicIndicator : NSObject
- (void)updateForStatus:(NSString *)status;
- (void)hide;
@end
