import SwiftUI

struct LoginView: View {
    @Environment(Session.self) private var session
    @State private var login = ""
    @State private var password = ""
    @State private var error: String?
    @State private var busy = false

    var body: some View {
        @Bindable var session = session
        VStack(spacing: 18) {
            Text("Envo Desk").font(.system(size: 26, weight: .semibold))
            Text("Вход в рабочее место").foregroundStyle(.secondary)
            Form {
                TextField("Сервер", text: $session.serverURL)
                    .textContentType(.URL)
                TextField("Логин", text: $login)
                    .textContentType(.username)
                SecureField("Пароль", text: $password)
                    .textContentType(.password)
                    .onSubmit { Task { await signIn() } }
            }
            .formStyle(.grouped)
            .frame(width: 380)
            if let error {
                Text(error).foregroundStyle(.red).font(.callout)
            }
            Button(busy ? "Входим…" : "Войти") { Task { await signIn() } }
                .keyboardShortcut(.defaultAction)
                .disabled(busy || login.isEmpty || password.isEmpty)
        }
        .padding(40)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private func signIn() async {
        busy = true
        defer { busy = false }
        do {
            try await session.signIn(login: login, password: password)
            error = nil
        } catch {
            self.error = error.localizedDescription
        }
    }
}
