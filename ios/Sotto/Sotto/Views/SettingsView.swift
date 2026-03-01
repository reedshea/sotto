import SwiftUI

struct SettingsView: View {
    @EnvironmentObject var destinationStore: DestinationStore
    @Environment(\.dismiss) private var dismiss

    @State private var name: String = ""
    @State private var url: String = ""
    @State private var keyID: String = ""
    @State private var keySecret: String = ""

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("Name", text: $name)
                        .textContentType(.organizationName)
                        .autocorrectionDisabled()

                    TextField("Server URL", text: $url)
                        .textContentType(.URL)
                        .keyboardType(.URL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()

                    TextField("Key ID", text: $keyID)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()

                    SecureField("Key Secret", text: $keySecret)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                } header: {
                    Text("Destination")
                } footer: {
                    Text("Recordings will be uploaded to this server automatically after capture.")
                }

                if destinationStore.isConfigured {
                    Section {
                        Button("Remove Destination", role: .destructive) {
                            destinationStore.clear()
                            name = ""
                            url = ""
                            keyID = ""
                            keySecret = ""
                        }
                    }
                }
            }
            .navigationTitle("Settings")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") {
                        let destination = Destination(
                            name: name,
                            url: url.trimmingCharacters(in: CharacterSet(charactersIn: "/")),
                            keyID: keyID,
                            keySecret: keySecret
                        )
                        destinationStore.save(destination)
                        dismiss()
                    }
                    .disabled(!isValid)
                }
            }
            .onAppear {
                if let existing = destinationStore.destination {
                    name = existing.name
                    url = existing.url
                    keyID = existing.keyID
                    keySecret = existing.keySecret
                }
            }
        }
    }

    private var isValid: Bool {
        !name.isEmpty && !url.isEmpty && !keySecret.isEmpty
    }
}
