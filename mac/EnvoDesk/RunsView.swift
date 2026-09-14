import SwiftUI

struct RunsView: View {
    var body: some View {
        Loader(load: { try await $0.runs() }) { runs, reload in
            VStack(spacing: 0) {
                HStack {
                    Text("Задачи и прогоны").font(.title2.weight(.semibold))
                    Spacer()
                    Button("Обновить", systemImage: "arrow.clockwise", action: reload).labelStyle(.iconOnly)
                }
                .padding(18)
                Table(runs) {
                    TableColumn("Когда") { Text(Fmt.short($0.startedAt)).monospacedDigit() }.width(90)
                    TableColumn("Задача") { Text($0.job) }.width(120)
                    TableColumn("Статус") { r in
                        Label(r.status == "ok" ? "ок" : r.status == "failed" ? "упал" : "идёт",
                              systemImage: r.status == "ok" ? "checkmark.circle" : r.status == "failed" ? "xmark.octagon" : "clock")
                            .foregroundStyle(r.status == "ok" ? .green : r.status == "failed" ? .red : .secondary)
                    }.width(90)
                    TableColumn("Ошибка") { Text($0.error ?? "").foregroundStyle(.red).lineLimit(1) }
                }
            }
        }
    }
}
