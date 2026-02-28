import Foundation

enum RecordingStatus: String, Codable {
    case savedLocally = "saved_locally"
    case uploading
    case uploaded
    case transcribing
    case done
    case uploadFailed = "upload_failed"
}

enum PrivacyMode: String, Codable {
    case `private`
    case standard
}

struct Recording: Identifiable, Codable {
    let id: UUID
    let capturedAt: Date
    var duration: TimeInterval
    var privacyMode: PrivacyMode
    var status: RecordingStatus
    var title: String?
    var summary: String?
    var localFileURL: URL?
    var serverUUID: String?

    var displayTitle: String {
        if let title = title {
            return title
        }
        let formatter = DateFormatter()
        formatter.dateStyle = .medium
        formatter.timeStyle = .short
        return "Recording — \(formatter.string(from: capturedAt))"
    }

    var formattedDuration: String {
        let minutes = Int(duration) / 60
        let seconds = Int(duration) % 60
        return String(format: "%d:%02d", minutes, seconds)
    }
}
