import Foundation

// Модели ответов API. Ключи в JSON — snake_case, декодер переводит в camelCase.

struct Overview: Decodable {
    struct KPIs: Decodable {
        let periodDays: Int
        let revenue: Double
        let tickets: Int
        let taxes: Double
        let profit: Double
        let profitIsBeforeCost: Bool
        let ticketsWithoutCost: Int
    }
    struct Daily: Decodable {
        struct Day: Decodable {
            let day: String
            let tickets: Int
            let by: [String: Double]
        }
        let series: [String]
        let days: [Day]
    }
    struct SellThrough: Decodable, Identifiable {
        let eventId: Int
        let event: String
        let startsAt: String
        let daysLeft: Int
        let bought: Int
        let sold: Int
        let left: Int
        let soldShare: Double?
        let elapsedShare: Double?
        var id: Int { eventId }
    }
    struct Attention: Decodable, Identifiable {
        let level: String
        let kind: String
        let title: String
        let detail: String
        var id: String { kind + title + detail }
    }
    struct Sale: Decodable, Identifiable {
        let order: String
        let at: String?
        let event: String
        let sector: String
        let tickets: Int
        let total: Double
        let buyer: String
        let refund: Bool
        var id: String { order }
    }
    let now: String
    let kpis: KPIs
    let daily: Daily
    let sellThrough: [SellThrough]
    let attention: [Attention]
    let recent: [Sale]
    let mailHoldAll: Bool
}

struct EventRow: Decodable, Identifiable, Hashable {
    let id: Int
    let afishaId: Int?
    let name: String
    let startsAt: String
    let tracking: Bool
}

struct EventCard: Decodable {
    struct PnL: Decodable {
        let invested: Double
        let revenue: Double
        let profit: Double
        let taxes: Double
        let platform: Double
        let frozen: Double
        let ticketsSold: Int
    }
    struct Step: Decodable, Identifiable {
        let idx: Int
        let price: Double
        let quota: Int
        let sold: Int
        let closed: Bool
        var id: Int { idx }
    }
    struct Category: Decodable, Identifiable {
        let id: Int
        let name: String
        let bought: Int
        let sold: Int
        let left: Int
        let cost: Double?
        let currentPrice: Double?
        let currentMargin: Double?
        let steps: [Step]
    }
    struct LogItem: Decodable, Identifiable {
        let at: String?
        let kind: String
        let ref: String
        let total: Double
        let tickets: Int
        let who: String?
        var id: String { ref + kind }
    }
    struct Fact: Decodable, Identifiable {
        let field: String
        let value: String
        let source: String
        let url: String?
        var id: String { field }
    }
    let id: Int
    let afishaId: Int?
    let title: String
    let displayName: String
    let startsAt: String
    let daysLeft: Int
    let tracking: Bool
    let hasLetter: Bool
    let pnl: PnL
    let categories: [Category]
    let log: [LogItem]
    let facts: [Fact]
}

struct OrdersPage: Decodable {
    struct Order: Decodable, Identifiable {
        let id: Int
        let order: String
        let at: String?
        let status: String
        let channel: String
        let total: Double
        let tickets: Int
        let sector: String
        let buyer: String
        let contactId: Int?
        let welcomeSentAt: String?
        let ticketsSentAt: String?
        let ticketsSentBy: String?
        let letters: Int
    }
    let orders: [Order]
    let nextBefore: String?
}

struct Letter: Decodable, Identifiable {
    let id: Int
    let kind: String
    let refId: String
    let toAddr: String
    let subject: String
    let bodyHtml: String
    let state: String
    let createdAt: String
    let sentAt: String?
    let attempts: Int
    let lastError: String?
}

struct Run: Decodable, Identifiable {
    let id: Int
    let job: String
    let startedAt: String
    let finishedAt: String?
    let status: String
    let error: String?
}

// MARK: форматирование

enum Fmt {
    static let rub: NumberFormatter = {
        let f = NumberFormatter()
        f.numberStyle = .decimal
        f.groupingSeparator = " "
        f.maximumFractionDigits = 0
        return f
    }()

    static func money(_ v: Double) -> String {
        if abs(v) >= 1_000_000 { return String(format: "%.2f млн ₽", v / 1_000_000).replacingOccurrences(of: ".", with: ",") }
        if abs(v) >= 10_000 { return "\(rub.string(from: NSNumber(value: (v / 1000).rounded())) ?? "") тыс ₽" }
        return "\(rub.string(from: NSNumber(value: v)) ?? "") ₽"
    }

    static func rubles(_ v: Double) -> String {
        "\(rub.string(from: NSNumber(value: v)) ?? "") ₽"
    }

    static func pct(_ v: Double?) -> String {
        guard let v else { return "—" }
        return "\(Int((v * 100).rounded()))%"
    }

    private static let iso = ISO8601DateFormatter()
    private static let isoFrac: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f
    }()
    private static let plain: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd'T'HH:mm:ss"
        f.timeZone = TimeZone(identifier: "Europe/Moscow")
        return f
    }()

    static func date(_ s: String?) -> Date? {
        guard let s else { return nil }
        return isoFrac.date(from: s) ?? iso.date(from: s) ?? plain.date(from: String(s.prefix(19)))
    }

    static func short(_ s: String?, time: Bool = true) -> String {
        guard let d = date(s) else { return "—" }
        let f = DateFormatter()
        f.locale = Locale(identifier: "ru_RU")
        f.timeZone = TimeZone(identifier: "Europe/Moscow")
        f.dateFormat = time ? "dd.MM HH:mm" : "dd.MM.yyyy"
        return f.string(from: d)
    }

    static func plural(_ n: Int, _ one: String, _ few: String, _ many: String) -> String {
        let m = abs(n) % 100, d = m % 10
        if m > 10 && m < 20 { return many }
        if d > 1 && d < 5 { return few }
        if d == 1 { return one }
        return many
    }
}
