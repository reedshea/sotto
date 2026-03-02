import Foundation

@MainActor
class TranscriptionStore: ObservableObject {
    @Published private(set) var transcriptions: [Transcription] = []
    @Published var serverConfig: ServerConfig {
        didSet { saveServerConfig() }
    }

    private static let transcriptionsKey = "sotto_dictation_transcriptions"
    private static let serverConfigKey = "sotto_dictation_server_config"
    private static let maxTranscriptions = 100

    init() {
        if let data = UserDefaults.standard.data(forKey: Self.serverConfigKey),
           let config = try? JSONDecoder().decode(ServerConfig.self, from: data) {
            self.serverConfig = config
        } else {
            self.serverConfig = .default
        }
        loadTranscriptions()
    }

    func add(_ transcription: Transcription) {
        transcriptions.insert(transcription, at: 0)
        if transcriptions.count > Self.maxTranscriptions {
            transcriptions = Array(transcriptions.prefix(Self.maxTranscriptions))
        }
        saveTranscriptions()
    }

    func clear() {
        transcriptions.removeAll()
        saveTranscriptions()
    }

    private func loadTranscriptions() {
        guard let data = UserDefaults.standard.data(forKey: Self.transcriptionsKey) else { return }
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        transcriptions = (try? decoder.decode([Transcription].self, from: data)) ?? []
    }

    private func saveTranscriptions() {
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        if let data = try? encoder.encode(transcriptions) {
            UserDefaults.standard.set(data, forKey: Self.transcriptionsKey)
        }
    }

    private func saveServerConfig() {
        if let data = try? JSONEncoder().encode(serverConfig) {
            UserDefaults.standard.set(data, forKey: Self.serverConfigKey)
        }
    }
}
