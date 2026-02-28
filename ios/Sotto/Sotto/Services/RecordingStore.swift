import Foundation

class RecordingStore: ObservableObject {
    @Published var recordings: [Recording] = []

    private let storageKey = "sotto_recordings"

    init() {
        load()
    }

    func add(_ recording: Recording) {
        recordings.insert(recording, at: 0)
        save()
    }

    func update(_ recording: Recording) {
        if let index = recordings.firstIndex(where: { $0.id == recording.id }) {
            recordings[index] = recording
            save()
        }
    }

    private func save() {
        if let data = try? JSONEncoder().encode(recordings) {
            UserDefaults.standard.set(data, forKey: storageKey)
        }
    }

    private func load() {
        guard let data = UserDefaults.standard.data(forKey: storageKey),
              let saved = try? JSONDecoder().decode([Recording].self, from: data)
        else { return }
        recordings = saved
    }
}
