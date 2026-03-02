import Foundation

struct Transcription: Identifiable, Codable {
    let id: UUID
    let text: String
    let durationSeconds: Double
    let createdAt: Date

    init(text: String, durationSeconds: Double) {
        self.id = UUID()
        self.text = text
        self.durationSeconds = durationSeconds
        self.createdAt = Date()
    }
}
