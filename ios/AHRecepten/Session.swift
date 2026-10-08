import Foundation
import Observation

/// Server address and login token, stored on the device.
@Observable
final class Session {
    var serverURL: String { didSet { UserDefaults.standard.set(serverURL, forKey: "serverURL") } }
    var token: String { didSet { UserDefaults.standard.set(token, forKey: "token") } }
    var connected: Bool { didSet { UserDefaults.standard.set(connected, forKey: "connected") } }

    init() {
        serverURL = UserDefaults.standard.string(forKey: "serverURL") ?? ""
        token = UserDefaults.standard.string(forKey: "token") ?? ""
        connected = UserDefaults.standard.bool(forKey: "connected")
    }

    var isConnected: Bool { connected && api != nil }

    var api: API? {
        guard let url = URL(string: serverURL.trimmingCharacters(in: .whitespaces)), url.scheme != nil else { return nil }
        return API(baseURL: url, token: token)
    }

    func logout() {
        connected = false
        token = ""
    }
}
