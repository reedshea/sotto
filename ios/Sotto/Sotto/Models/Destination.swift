import Foundation

struct Destination: Identifiable, Codable {
    let id: UUID
    var name: String
    var url: String
    var keyID: String
    var keySecret: String

    init(id: UUID = UUID(), name: String, url: String, keyID: String, keySecret: String) {
        self.id = id
        self.name = name
        self.url = url
        self.keyID = keyID
        self.keySecret = keySecret
    }
}
