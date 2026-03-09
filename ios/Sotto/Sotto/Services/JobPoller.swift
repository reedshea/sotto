import Combine
import Foundation

struct JobStatusResponse: Decodable {
    let uuid: String
    let status: String
    let title: String?
    let summary: String?
    let transcript: String?
    let durationSeconds: Double?
    let errorMessage: String?
    let replyTo: String?

    enum CodingKeys: String, CodingKey {
        case uuid, status, title, summary, transcript
        case durationSeconds = "duration_seconds"
        case errorMessage = "error_message"
        case replyTo = "reply_to"
    }
}

class JobPoller: ObservableObject {
    private var timer: Timer?
    private let interval: TimeInterval = 3.0
    private weak var store: RecordingStore?
    private weak var destinationStore: DestinationStore?

    init(store: RecordingStore, destinationStore: DestinationStore) {
        self.store = store
        self.destinationStore = destinationStore
    }

    func startIfNeeded() {
        guard timer == nil else { return }
        guard hasActiveJobs else { return }
        timer = Timer.scheduledTimer(withTimeInterval: interval, repeats: true) { [weak self] _ in
            self?.pollActiveJobs()
        }
    }

    func stop() {
        timer?.invalidate()
        timer = nil
    }

    private var hasActiveJobs: Bool {
        store?.recordings.contains { $0.serverUUID != nil && ($0.status == .uploaded || $0.status == .transcribing) } ?? false
    }

    private func pollActiveJobs() {
        guard let store, let destinationStore, let destination = destinationStore.destination else { return }

        let active = store.recordings.filter { r in
            r.serverUUID != nil && (r.status == .uploaded || r.status == .transcribing)
        }

        if active.isEmpty {
            stop()
            return
        }

        for recording in active {
            Task { await pollJob(recording, destination: destination) }
        }
    }

    @MainActor
    private func pollJob(_ recording: Recording, destination: Destination) async {
        guard let serverUUID = recording.serverUUID else { return }

        var request = URLRequest(url: URL(string: "\(destination.url)/jobs/\(serverUUID)")!)
        request.setValue("Bearer \(destination.keySecret)", forHTTPHeaderField: "Authorization")

        guard let (data, _) = try? await URLSession.shared.data(for: request),
              let response = try? JSONDecoder().decode(JobStatusResponse.self, from: data)
        else { return }

        let newStatus = RecordingStatus.fromServer(response.status)
        guard newStatus != recording.status || response.title != recording.title else { return }

        var updated = recording
        updated.status = newStatus
        if let title = response.title { updated.title = title }
        if let summary = response.summary { updated.summary = summary }
        if let replyTo = response.replyTo { updated.replyTo = replyTo }
        store?.update(updated)

        if !hasActiveJobs { stop() }
    }
}
