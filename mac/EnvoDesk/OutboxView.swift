import SwiftUI
import WebKit

struct OutboxView: View {
    @Environment(Session.self) private var session
    @State private var state = "queued"
    @State private var selected: Letter?

    var body: some View {
        Loader(load: { try await $0.letters(state: state) }) { letters, reload in
            HSplitView {
                VStack(spacing: 0) {
                    Picker("", selection: $state) {
                        Text("В очереди").tag("queued"); Text("Удержаны").tag("held")
                        Text("Отправлены").tag("sent"); Text("Не ушли").tag("failed")
                    }
                    .pickerStyle(.segmented).padding(10)
                    .onChange(of: state) { selected = nil; reload() }
                    List(letters, selection: $selected) { l in
                        VStack(alignment: .leading, spacing: 2) {
                            Text(l.subject).font(.callout.weight(.medium)).lineLimit(1)
                            Text("\(l.toAddr) · \(l.kind == "welcome" ? "вэлком" : l.kind == "cart" ? "догон" : l.kind) · \(Fmt.short(l.createdAt))")
                                .font(.caption).foregroundStyle(.secondary)
                            if let e = l.lastError { Text(e).font(.caption2).foregroundStyle(.red).lineLimit(1) }
                        }
                        .tag(l)
                    }
                    .overlay { if letters.isEmpty { Text("Пусто").foregroundStyle(.secondary) } }
                }
                .frame(minWidth: 300, idealWidth: 340, maxWidth: 420)
                Group {
                    if let l = selected {
                        VStack(spacing: 0) {
                            HStack {
                                Text(l.subject).font(.headline)
                                Spacer()
                                if l.state == "queued" || l.state == "held" {
                                    Button("Придержать") { Task { await act(l, "hold"); reload() } }.disabled(l.state == "held")
                                    Button("Отправить") { Task { await act(l, "release"); reload() } }.disabled(l.state == "queued")
                                    Button("Никогда", role: .destructive) { Task { await act(l, "skip"); reload() } }
                                }
                            }
                            .padding(12)
                            Divider()
                            HTMLView(html: l.bodyHtml)
                        }
                    } else {
                        ContentUnavailableView("Выбери письмо", systemImage: "envelope")
                    }
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
            .id(state)
        }
    }

    private func act(_ l: Letter, _ action: String) async {
        try? await session.api.letterAction(l.id, action)
        selected = nil
    }
}

extension Letter: Hashable {
    static func == (a: Letter, b: Letter) -> Bool { a.id == b.id }
    func hash(into h: inout Hasher) { h.combine(id) }
}

struct HTMLView: NSViewRepresentable {
    let html: String
    func makeNSView(context: Context) -> WKWebView { WKWebView() }
    func updateNSView(_ view: WKWebView, context: Context) {
        view.loadHTMLString("<meta charset=utf-8><body style='margin:16px'>" + html, baseURL: nil)
    }
}
