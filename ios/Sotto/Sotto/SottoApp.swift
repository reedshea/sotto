import SwiftUI

@main
struct SottoApp: App {
    @StateObject private var recordingStore = RecordingStore()
    @StateObject private var destinationStore = DestinationStore()

    var body: some Scene {
        WindowGroup {
            RecordingListView()
                .environmentObject(recordingStore)
                .environmentObject(destinationStore)
        }
    }
}
