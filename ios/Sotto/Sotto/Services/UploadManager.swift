import Foundation

struct UploadResponse: Decodable {
    let uuid: String
    let status: String
}

class UploadManager {
    static let shared = UploadManager()

    func upload(recording: Recording, destination: Destination) async throws -> UploadResponse {
        guard let fileURL = recording.localFileURL else {
            throw URLError(.fileDoesNotExist)
        }

        var request = URLRequest(url: URL(string: "\(destination.url)/upload")!)
        request.httpMethod = "POST"
        request.setValue("Bearer \(destination.keySecret)", forHTTPHeaderField: "Authorization")

        let boundary = UUID().uuidString
        request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")

        var body = Data()

        // Privacy field
        body.append("--\(boundary)\r\n".data(using: .utf8)!)
        body.append("Content-Disposition: form-data; name=\"privacy\"\r\n\r\n".data(using: .utf8)!)
        body.append("\(recording.privacyMode.rawValue)\r\n".data(using: .utf8)!)

        // File field
        body.append("--\(boundary)\r\n".data(using: .utf8)!)
        body.append("Content-Disposition: form-data; name=\"file\"; filename=\"\(fileURL.lastPathComponent)\"\r\n".data(using: .utf8)!)
        body.append("Content-Type: audio/m4a\r\n\r\n".data(using: .utf8)!)
        if let audioData = try? Data(contentsOf: fileURL) {
            body.append(audioData)
        }
        body.append("\r\n--\(boundary)--\r\n".data(using: .utf8)!)

        let (data, response) = try await URLSession.shared.upload(for: request, from: body)

        guard let http = response as? HTTPURLResponse, http.statusCode == 201 else {
            throw URLError(.badServerResponse)
        }

        return try JSONDecoder().decode(UploadResponse.self, from: data)
    }
}
