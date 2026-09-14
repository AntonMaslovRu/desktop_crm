import Foundation
import Observation
import Security

/// Состояние входа: адрес сервера и токен. Токен живёт в Keychain, адрес — в UserDefaults.
@Observable
final class Session {
    var serverURL: String
    private(set) var token: String?
    var login: String?

    var isLoggedIn: Bool { token != nil }
    var api: API { API(baseURL: URL(string: serverURL) ?? URL(string: "https://api.envo.live")!, token: token) }

    init() {
        serverURL = UserDefaults.standard.string(forKey: "serverURL") ?? "https://api.envo.live"
        token = Keychain.read("token")
    }

    func persist() {
        UserDefaults.standard.set(serverURL, forKey: "serverURL")
    }

    func signIn(login: String, password: String) async throws {
        persist()
        let response = try await api.login(login: login, password: password)
        Keychain.write("token", response.token)
        token = response.token
        self.login = login
    }

    func signOut() async {
        try? await api.logout()
        Keychain.delete("token")
        token = nil
    }

    /// Сервер ответил 401 — токен протух, показываем вход.
    func expire() {
        Keychain.delete("token")
        token = nil
    }
}

enum Keychain {
    private static let service = "live.envo.desk"

    static func read(_ key: String) -> String? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: key,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var item: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &item) == errSecSuccess,
              let data = item as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    static func write(_ key: String, _ value: String) {
        delete(key)
        let item: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: key,
            kSecValueData as String: Data(value.utf8),
            kSecAttrAccessible as String: kSecAttrAccessibleWhenUnlockedThisDeviceOnly,
        ]
        SecItemAdd(item as CFDictionary, nil)
    }

    static func delete(_ key: String) {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: key,
        ]
        SecItemDelete(query as CFDictionary)
    }
}
