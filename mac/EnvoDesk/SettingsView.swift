import SwiftUI

struct SettingsView: View {
    @Environment(Session.self) private var session
    @State private var holdAll = false
    @State private var message: String?

    var body: some View {
        @Bindable var session = session
        Form {
            Section("Сервер") {
                TextField("Адрес API", text: $session.serverURL)
                    .onChange(of: session.serverURL) { session.persist() }
                LabeledContent("Пользователь", value: session.login ?? "—")
            }
            Section("Почта") {
                Toggle("Стоп-кран: ничего не отправлять", isOn: $holdAll)
                    .onChange(of: holdAll) { _, on in
                        Task {
                            do { try await session.api.holdAll(on); message = on ? "Письма копятся в очереди" : "Отправка включена" }
                            catch { message = error.localizedDescription }
                        }
                    }
                Text("Письма остаются в очереди со статусом «удержано», пока стоп-кран включён.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            Section {
                Button("Выйти", role: .destructive) { Task { await session.signOut() } }
            }
            if let message { Text(message).font(.callout).foregroundStyle(.secondary) }
        }
        .formStyle(.grouped)
        .task {
            if let o = try? await session.api.overview(period: 1) { holdAll = o.mailHoldAll }
        }
    }
}
