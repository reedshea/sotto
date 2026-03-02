import SwiftUI

struct SettingsView: View {
    @EnvironmentObject var store: TranscriptionStore
    @Environment(\.dismiss) private var dismiss
    @State private var url: String = ""
    @State private var token: String = ""
    @State private var healthStatus: HealthStatus = .unknown

    enum HealthStatus {
        case unknown, checking, healthy, unhealthy(String)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Sotto Server")
                .font(.headline)

            VStack(alignment: .leading, spacing: 8) {
                Text("Server URL")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                TextField("http://localhost:8377", text: $url)
                    .textFieldStyle(.roundedBorder)

                Text("Auth Token")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                SecureField("Bearer token (optional)", text: $token)
                    .textFieldStyle(.roundedBorder)
            }

            HStack {
                Button("Test Connection") {
                    testConnection()
                }

                switch healthStatus {
                case .unknown:
                    EmptyView()
                case .checking:
                    ProgressView()
                        .controlSize(.small)
                case .healthy:
                    Label("Connected", systemImage: "checkmark.circle.fill")
                        .foregroundStyle(.green)
                        .font(.caption)
                case .unhealthy(let msg):
                    Label(msg, systemImage: "exclamationmark.triangle.fill")
                        .foregroundStyle(.red)
                        .font(.caption)
                        .lineLimit(1)
                }
            }

            Divider()

            Text("Hotkey: \u{2303}\u{2325}S (Control + Option + S)")
                .font(.caption)
                .foregroundStyle(.secondary)

            Text("Transcription is automatically pasted into the active text field when complete.")
                .font(.caption)
                .foregroundStyle(.secondary)

            Spacer()

            HStack {
                Spacer()
                Button("Cancel") { dismiss() }
                    .keyboardShortcut(.cancelAction)
                Button("Save") {
                    store.serverConfig = ServerConfig(url: url, token: token)
                    dismiss()
                }
                .keyboardShortcut(.defaultAction)
            }
        }
        .padding(20)
        .frame(width: 380, height: 340)
        .onAppear {
            url = store.serverConfig.url
            token = store.serverConfig.token
        }
    }

    private func testConnection() {
        healthStatus = .checking
        guard let healthURL = URL(string: "\(url)/health") else {
            healthStatus = .unhealthy("Invalid URL")
            return
        }
        var request = URLRequest(url: healthURL)
        request.timeoutInterval = 5
        if !token.isEmpty {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        Task {
            do {
                let (_, response) = try await URLSession.shared.data(for: request)
                let code = (response as? HTTPURLResponse)?.statusCode ?? 0
                await MainActor.run {
                    healthStatus = code == 200 ? .healthy : .unhealthy("HTTP \(code)")
                }
            } catch {
                await MainActor.run {
                    healthStatus = .unhealthy(error.localizedDescription)
                }
            }
        }
    }
}
