import SwiftUI

enum Screen: String, CaseIterable, Identifiable {
    case overview = "Обзор"
    case events = "События"
    case outbox = "Письма"
    case runs = "Задачи и прогоны"
    case settings = "Настройки"
    var id: String { rawValue }
    var icon: String {
        switch self {
        case .overview: return "chart.bar.doc.horizontal"
        case .events: return "ticket"
        case .outbox: return "envelope"
        case .runs: return "clock.arrow.2.circlepath"
        case .settings: return "gearshape"
        }
    }
}

struct MainView: View {
    @Environment(Session.self) private var session
    @State private var section: Screen? = .overview
    @State private var selectedEvent: EventRow?

    var body: some View {
        NavigationSplitView {
            List(selection: $section) {
                ForEach(Screen.allCases) { s in
                    Label(s.rawValue, systemImage: s.icon).tag(s)
                }
            }
            .navigationSplitViewColumnWidth(min: 180, ideal: 200)
        } detail: {
            switch section ?? .overview {
            case .overview: OverviewView(openEvent: { id in
                selectedEvent = EventRow(id: id, afishaId: nil, name: "", startsAt: "", tracking: true)
                section = .events
            })
            case .events: EventsView(selected: $selectedEvent)
            case .outbox: OutboxView()
            case .runs: RunsView()
            case .settings: SettingsView()
            }
        }
        .navigationTitle(section?.rawValue ?? "Envo Desk")
    }
}

/// Загрузка с обработкой 401: протухший токен возвращает на вход.
struct Loader<Content: View, T>: View {
    @Environment(Session.self) private var session
    let load: (API) async throws -> T
    @ViewBuilder let content: (T, @escaping () -> Void) -> Content
    @State private var value: T?
    @State private var error: String?

    var body: some View {
        Group {
            if let value {
                content(value) { Task { await run() } }
            } else if let error {
                ContentUnavailableView("Не загрузилось", systemImage: "wifi.exclamationmark", description: Text(error))
            } else {
                ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .task { await run() }
    }

    private func run() async {
        do {
            value = try await load(session.api)
            error = nil
        } catch API.Failure.unauthorized {
            session.expire()
        } catch {
            self.error = error.localizedDescription
        }
    }
}

struct Card<Content: View>: View {
    let title: String
    var hint: String? = nil
    @ViewBuilder let content: () -> Content

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline, spacing: 10) {
                Text(title).font(.headline)
                if let hint { Text(hint).font(.caption).foregroundStyle(.secondary) }
            }
            content()
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.background, in: RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(.quaternary))
    }
}
