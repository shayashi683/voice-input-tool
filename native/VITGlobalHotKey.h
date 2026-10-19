#import <Cocoa/Cocoa.h>

@interface VITGlobalHotKey : NSObject
- (instancetype)initWithHandler:(void (^)(void))handler;
- (BOOL)registerHotKey:(NSString *)hotKey;
- (void)invalidate;
@end
