import SwiftUI

@main
struct EnvoDeskApp: App {
    @State private var session = Session()

    var body: some Scene {
        WindowGroup("Envo Desk") {
            RootView()
                .environment(session)
                .frame(minWidth: 1040, minHeight: 680)
        }
        .windowResizability(.contentMinSize)
        .commands {
            CommandGroup(replacing: .newItem) {}
        }
    }
}

struct RootView: View {
    @Environment(Session.self) private var session

    var body: some View {
        if session.isLoggedIn {
            MainView()
        } else {
            LoginView()
        }
    }
}
