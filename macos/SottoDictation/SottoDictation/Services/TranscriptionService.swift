import Foundation

struct TranscriptionResponse: Decodable {
    let text: String
    let duration_seconds: Double
}

enum TranscriptionError: LocalizedError {
    case notConfigured
    case invalidURL
    case serverError(Int, String)
    case networkError(Error)

    var errorDescription: String? {
        switch self {
        case .notConfigured: return "Server not configured"
        case .invalidURL: return "Invalid server URL"
        case .serverError(let code, let msg): return "Server error \(code): \(msg)"
        case .networkError(let err): return err.localizedDescription
        }
    }
}

class TranscriptionService {
    /// Send an audio file to the Sotto server for synchronous transcription.
    func transcribe(fileURL: URL, config: ServerConfig) async throws -> TranscriptionResponse {
        guard config.isConfigured else { throw TranscriptionError.notConfigured }
        guard let url = config.transcribeURL else { throw TranscriptionError.invalidURL }

        let boundary = UUID().uuidString
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        if !config.token.isEmpty {
            request.setValue("Bearer \(config.token)", forHTTPHeaderField: "Authorization")
        }

        let audioData = try Data(contentsOf: fileURL)
        var body = Data()
        body.append("--\(boundary)\r\n".data(using: .utf8)!)
        body.append("Content-Disposition: form-data; name=\"file\"; filename=\"recording.m4a\"\r\n".data(using: .utf8)!)
        body.append("Content-Type: audio/mp4\r\n\r\n".data(using: .utf8)!)
        body.append(audioData)
        body.append("\r\n--\(boundary)--\r\n".data(using: .utf8)!)
        request.httpBody = body

        let (data, response): (Data, URLResponse)
        do {
            (data, response) = try await URLSession.shared.data(for: request)
        } catch {
            throw TranscriptionError.networkError(error)
        }

        let httpResponse = response as! HTTPURLResponse
        guard httpResponse.statusCode == 200 else {
            let msg = String(data: data, encoding: .utf8) ?? "Unknown error"
            throw TranscriptionError.serverError(httpResponse.statusCode, msg)
        }

        return try JSONDecoder().decode(TranscriptionResponse.self, from: data)
    }
}
