import Foundation

class UploadManager: ObservableObject {
    static let shared = UploadManager()

    private lazy var session: URLSession = {
        let config = URLSessionConfiguration.background(withIdentifier: "com.sotto.upload")
        config.isDiscretionary = false
        config.sessionSendsLaunchEvents = true
        return URLSession(configuration: config, delegate: nil, delegateQueue: nil)
    }()

    func upload(recording: Recording, destination: Destination) {
        guard let fileURL = recording.localFileURL else { return }

        var request = URLRequest(url: URL(string: "\(destination.url)/upload")!)
        request.httpMethod = "POST"
        request.setValue("Bearer \(destination.keySecret)", forHTTPHeaderField: "Authorization")

        let boundary = UUID().uuidString
        request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")

        let tempURL = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
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

        try? body.write(to: tempURL)

        session.uploadTask(with: request, fromFile: tempURL).resume()
    }
}
