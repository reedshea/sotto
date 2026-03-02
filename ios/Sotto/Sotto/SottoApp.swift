import SwiftUI

@main
struct SottoApp: App {
    @StateObject private var recordingStore: RecordingStore
    @StateObject private var destinationStore: DestinationStore
    @StateObject private var poller: JobPoller

    init() {
        let store = RecordingStore()
        let destStore = DestinationStore()
        _recordingStore = StateObject(wrappedValue: store)
        _destinationStore = StateObject(wrappedValue: destStore)
        _poller = StateObject(wrappedValue: JobPoller(store: store, destinationStore: destStore))
    }

    var body: some Scene {
        WindowGroup {
            RecordingListView()
                .environmentObject(recordingStore)
                .environmentObject(destinationStore)
                .environmentObject(poller)
                .onAppear { poller.startIfNeeded() }
        }
    }
}
