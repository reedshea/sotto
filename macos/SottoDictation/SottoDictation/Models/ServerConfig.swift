import Foundation

struct ServerConfig: Codable {
    var url: String
    var token: String

    static let `default` = ServerConfig(url: "http://localhost:8377", token: "")

    var transcribeURL: URL? {
        URL(string: "\(url)/transcribe")
    }

    var healthURL: URL? {
        URL(string: "\(url)/health")
    }

    var isConfigured: Bool {
        !url.isEmpty
    }
}
