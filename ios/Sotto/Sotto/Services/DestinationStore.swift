import Foundation

class DestinationStore: ObservableObject {
    @Published var destination: Destination?

    private let storageKey = "sotto_destination"

    init() {
        load()
    }

    var isConfigured: Bool {
        destination != nil
    }

    func save(_ destination: Destination) {
        self.destination = destination
        if let data = try? JSONEncoder().encode(destination) {
            UserDefaults.standard.set(data, forKey: storageKey)
        }
    }

    func clear() {
        destination = nil
        UserDefaults.standard.removeObject(forKey: storageKey)
    }

    private func load() {
        guard let data = UserDefaults.standard.data(forKey: storageKey),
              let saved = try? JSONDecoder().decode(Destination.self, from: data)
        else { return }
        destination = saved
    }
}
