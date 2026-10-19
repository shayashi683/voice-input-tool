// Exercise the actual native output reader with split UTF-8/JSON; no UI or paste.
#define main VoiceInputAppMain
#import "../native/native_status_app.m"
#undef main

@interface ReaderTestDelegate : AppDelegate
@property(nonatomic, strong) NSMutableArray<NSString *> *lines;
@end
@implementation ReaderTestDelegate
- (void)writePasteReadyHeartbeatIfNeeded {}
- (void)pollStatusFile {}
- (void)animateStatusIndicator {}
- (void)handleOutputLine:(NSString *)line { [self.lines addObject:line]; }
@end

int main(void) {
    @autoreleasepool {
        ReaderTestDelegate *reader = [ReaderTestDelegate new];
        reader.lines = [NSMutableArray array];
        reader.pendingOutput = [NSMutableData data];
        VITEnsureParentDirectory(VITOutputFilePath());
        [[NSData data] writeToFile:VITOutputFilePath() atomically:YES];
        NSFileHandle *writer = [NSFileHandle fileHandleForWritingAtPath:VITOutputFilePath()];
        NSString *payload = @"{\"text\":\"途中で消えない日本語\"}";
        NSData *encoded = [payload dataUsingEncoding:NSUTF8StringEncoding];
        for (NSUInteger index = 0; index < encoded.length; index++) {
            [writer writeData:[encoded subdataWithRange:NSMakeRange(index, 1)]];
            [reader pollOutputFile:nil];
            NSCAssert(reader.lines.count == 0, @"Partial output must be retained");
        }
        [writer writeData:[@"\n" dataUsingEncoding:NSUTF8StringEncoding]];
        [reader pollOutputFile:nil];
        NSCAssert(reader.lines.count == 1 && [reader.lines[0] isEqualToString:payload], @"Split UTF-8 must round trip");
        [writer writeData:[@"two\nthree\npartial" dataUsingEncoding:NSUTF8StringEncoding]];
        [reader pollOutputFile:nil];
        NSCAssert(reader.lines.count == 3, @"All complete lines must be consumed");
        [writer truncateFileAtOffset:0];
        [writer seekToFileOffset:0];
        [writer writeData:[@"new\n" dataUsingEncoding:NSUTF8StringEncoding]];
        [reader pollOutputFile:nil];
        NSCAssert(reader.lines.count == 4 && [reader.lines[3] isEqualToString:@"new"], @"Truncation must clear pending bytes");
        [writer closeFile];
        puts("Native output: split UTF-8, incomplete JSON, batching and truncation passed");
    }
    return 0;
}
