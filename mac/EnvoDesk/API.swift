import Foundation

/// Тонкий клиент к API ядра. Один метод на роут, ошибки — человеческим языком.
struct API {
    let baseURL: URL
    let token: String?

    enum Failure: LocalizedError {
        case unauthorized
        case server(Int, String)
        case network(String)

        var errorDescription: String? {
            switch self {
            case .unauthorized: return "Нужен вход"
            case let .server(code, text): return "Сервер ответил \(code): \(text)"
            case let .network(text): return "Нет связи: \(text)"
            }
        }
    }

    private static let decoder: JSONDecoder = {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        return d
    }()

    private func request<T: Decodable>(_ method: String, _ path: String,
                                       query: [String: String] = [:]) async throws -> T {
        try await send(method, path, query: query, bodyData: nil)
    }

    private func request<T: Decodable, B: Encodable>(_ method: String, _ path: String,
                                                     query: [String: String] = [:],
                                                     body: B) async throws -> T {
        try await send(method, path, query: query, bodyData: try JSONEncoder().encode(body))
    }

    private func send<T: Decodable>(_ method: String, _ path: String, query: [String: String],
                                    bodyData: Data?) async throws -> T {
        var components = URLComponents(url: baseURL.appending(path: path), resolvingAgainstBaseURL: false)!
        if !query.isEmpty {
            components.queryItems = query.map { URLQueryItem(name: $0.key, value: $0.value) }
        }
        var req = URLRequest(url: components.url!)
        req.httpMethod = method
        req.setValue("application/json", forHTTPHeaderField: "Accept")
        if let token { req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization") }
        if let bodyData {
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            req.httpBody = bodyData
        }
        let (data, response): (Data, URLResponse)
        do {
            (data, response) = try await URLSession.shared.data(for: req)
        } catch {
            throw Failure.network(error.localizedDescription)
        }
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
        if status == 401 { throw Failure.unauthorized }
        guard (200..<300).contains(status) else {
            let text = (try? JSONDecoder().decode([String: String].self, from: data))?["detail"]
                ?? String(data: data, encoding: .utf8) ?? ""
            throw Failure.server(status, text)
        }
        return try Self.decoder.decode(T.self, from: data)
    }

    // MARK: роуты

    struct LoginResponse: Decodable { let token: String }
    struct Ok: Decodable { let ok: Bool }

    func login(login: String, password: String) async throws -> LoginResponse {
        struct Body: Encodable { let login: String; let password: String; let device: String }
        let device = Host.current().localizedName ?? "Mac"
        return try await request("POST", "/auth/login", body: Body(login: login, password: password, device: device))
    }

    func logout() async throws {
        let _: Ok = try await request("POST", "/auth/logout")
    }

    func overview(period: Int) async throws -> Overview {
        try await request("GET", "/overview", query: ["period": String(period)])
    }

    func events() async throws -> [EventRow] {
        try await request("GET", "/events")
    }

    func event(_ id: Int) async throws -> EventCard {
        try await request("GET", "/events/\(id)")
    }

    func orders(event id: Int, before: String?) async throws -> OrdersPage {
        var q = ["limit": "30"]
        if let before { q["before"] = before }
        return try await request("GET", "/events/\(id)/orders", query: q)
    }

    func letters(state: String) async throws -> [Letter] {
        try await request("GET", "/letters", query: ["state": state])
    }

    func letterAction(_ id: Int, _ action: String) async throws {
        struct Body: Encodable { let action: String }
        let _: [String: AnyCodable] = try await request("POST", "/letters/\(id)", body: Body(action: action))
    }

    func holdAll(_ on: Bool) async throws {
        struct Body: Encodable { let on: Bool }
        let _: [String: AnyCodable] = try await request("POST", "/mail/hold-all", body: Body(on: on))
    }

    func ticketsSent(order id: Int, _ sent: Bool) async throws {
        struct Body: Encodable { let sent: Bool }
        let _: Ok = try await request("POST", "/orders/\(id)/tickets-sent", body: Body(sent: sent))
    }

    func runs() async throws -> [Run] {
        try await request("GET", "/runs")
    }
}

/// Для ответов вида {"ok": true, "state": "held"} — значения разных типов.
struct AnyCodable: Decodable {
    init(from decoder: Decoder) throws {}
}
