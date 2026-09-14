import Charts
import SwiftUI

struct OverviewView: View {
    var openEvent: (Int) -> Void
    @State private var period = 30

    var body: some View {
        Loader(load: { try await $0.overview(period: period) }) { data, reload in
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    HStack {
                        Text("Обзор").font(.title2.weight(.semibold))
                        Spacer()
                        Picker("Период", selection: $period) {
                            Text("Сегодня").tag(1); Text("7 дней").tag(7); Text("30 дней").tag(30)
                        }
                        .pickerStyle(.segmented).frame(width: 260)
                        .onChange(of: period) { reload() }
                        Button("Обновить", systemImage: "arrow.clockwise", action: reload).labelStyle(.iconOnly)
                    }
                    if data.mailHoldAll {
                        Label("Стоп-кран почты включён: письма копятся в очереди", systemImage: "hand.raised.fill")
                            .padding(10).frame(maxWidth: .infinity, alignment: .leading)
                            .background(.yellow.opacity(0.15), in: RoundedRectangle(cornerRadius: 8))
                    }
                    HStack(alignment: .top, spacing: 14) {
                        VStack(spacing: 14) {
                            kpis(data.kpis)
                            Card(title: "Выручка по дням", hint: "стопкой по событиям") { dailyChart(data.daily) }
                            Card(title: "Успеваем распродать", hint: "полоса — продано, метка — прошло времени") {
                                sellThrough(data.sellThrough)
                            }
                        }
                        VStack(spacing: 14) {
                            Card(title: "Требует внимания") { attention(data.attention) }
                            Card(title: "Последние продажи") { recent(data.recent) }
                        }
                        .frame(width: 300)
                    }
                }
                .padding(18)
            }
            .id(period)
        }
    }

    private func kpis(_ k: Overview.KPIs) -> some View {
        HStack(spacing: 10) {
            tile("Выручка", Fmt.money(k.revenue), "\(k.tickets) \(Fmt.plural(k.tickets, "билет", "билета", "билетов"))")
            tile("Прибыль", Fmt.money(k.profit),
                 k.profitIsBeforeCost ? "у \(k.ticketsWithoutCost) билетов нет себестоимости" : "после сборов, налогов и закупки")
            tile("Налоги", Fmt.money(k.taxes), "УСН 7% + НДС 5%")
            tile("Билетов", "\(k.tickets) шт", "за \(k.periodDays) \(Fmt.plural(k.periodDays, "день", "дня", "дней"))")
        }
    }

    private func tile(_ label: String, _ value: String, _ sub: String) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(label.uppercased()).font(.caption2.weight(.semibold)).foregroundStyle(.secondary)
            Text(value).font(.title3.weight(.semibold)).monospacedDigit()
            Text(sub).font(.caption).foregroundStyle(.secondary).lineLimit(2)
        }
        .padding(12).frame(maxWidth: .infinity, alignment: .leading)
        .background(.background, in: RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(.quaternary))
    }

    private struct Bar: Identifiable {
        let day: String; let series: String; let value: Double
        var id: String { day + series }
    }

    private func dailyChart(_ d: Overview.Daily) -> some View {
        let bars = d.days.flatMap { day in
            d.series.compactMap { s -> Bar? in
                guard let v = day.by[s] else { return nil }
                return Bar(day: String(day.day.suffix(5)), series: s, value: v)
            }
        }
        return Chart(bars) { b in
            BarMark(x: .value("День", b.day), y: .value("₽", b.value))
                .foregroundStyle(by: .value("Событие", b.series))
                .cornerRadius(2)
        }
        .chartYAxis { AxisMarks { v in AxisGridLine(); AxisValueLabel { if let n = v.as(Double.self) { Text("\(Int(n / 1000))к") } } } }
        .chartLegend(position: .top, alignment: .leading)
        .frame(height: 190)
        .overlay { if bars.isEmpty { Text("Продаж за период нет").foregroundStyle(.secondary) } }
    }

    private func sellThrough(_ rows: [Overview.SellThrough]) -> some View {
        VStack(spacing: 8) {
            if rows.isEmpty { Text("Нет событий в трекинге").foregroundStyle(.secondary) }
            ForEach(rows) { r in
                HStack(spacing: 12) {
                    VStack(alignment: .leading, spacing: 1) {
                        Text(r.event).font(.callout.weight(.medium)).lineLimit(1)
                        Text("\(Fmt.short(r.startsAt, time: false)) · осталось \(r.left)").font(.caption2).foregroundStyle(.secondary)
                    }
                    .frame(width: 170, alignment: .leading)
                    GeometryReader { g in
                        ZStack(alignment: .leading) {
                            Capsule().fill(.quaternary)
                            Capsule().fill(.blue).frame(width: max(3, g.size.width * (r.soldShare ?? 0)))
                            if let e = r.elapsedShare {
                                RoundedRectangle(cornerRadius: 1.5).fill(.primary)
                                    .frame(width: 3, height: 20).offset(x: g.size.width * e - 1.5)
                            }
                        }
                    }
                    .frame(height: 12)
                    Text("\(Fmt.pct(r.soldShare)) / срок \(Fmt.pct(r.elapsedShare))")
                        .font(.caption.monospacedDigit()).foregroundStyle(.secondary).frame(width: 110, alignment: .leading)
                    status(r)
                }
                .contentShape(Rectangle())
                .onTapGesture { openEvent(r.eventId) }
            }
        }
    }

    private func status(_ r: Overview.SellThrough) -> some View {
        let gap = (r.soldShare ?? 0) - (r.elapsedShare ?? 0)
        let (text, color, icon): (String, Color, String) = gap > 0.12 ? ("опережаем", .green, "arrowtriangle.up.fill")
            : gap < -0.12 ? ("отстаём", .red, "arrowtriangle.down.fill") : ("в темпе", .secondary, "minus")
        return Label(text, systemImage: icon).font(.caption.weight(.medium)).foregroundStyle(color).frame(width: 100, alignment: .leading)
    }

    private func attention(_ items: [Overview.Attention]) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            if items.isEmpty { Label("Всё чисто", systemImage: "checkmark.circle").foregroundStyle(.green) }
            ForEach(items) { a in
                HStack(alignment: .top, spacing: 8) {
                    Image(systemName: a.level == "critical" ? "exclamationmark.circle.fill" : a.level == "warning" ? "exclamationmark.triangle.fill" : "info.circle")
                        .foregroundStyle(a.level == "critical" ? .red : a.level == "warning" ? .orange : .secondary)
                    VStack(alignment: .leading, spacing: 1) {
                        Text(a.title).font(.callout.weight(.semibold))
                        Text(a.detail).font(.caption).foregroundStyle(.secondary)
                    }
                }
                Divider()
            }
        }
    }

    private func recent(_ sales: [Overview.Sale]) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            if sales.isEmpty { Text("Продаж пока нет").foregroundStyle(.secondary) }
            ForEach(sales) { s in
                VStack(alignment: .leading, spacing: 1) {
                    HStack(spacing: 6) {
                        Text(Fmt.short(s.at)).font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                        Text(s.event).font(.callout.weight(.medium)).foregroundStyle(s.refund ? .red : .primary).lineLimit(1)
                    }
                    HStack(spacing: 8) {
                        Text(s.sector).lineLimit(1)
                        Text((s.refund ? "−" : "") + Fmt.rubles(s.total)).monospacedDigit()
                        Text(s.buyer)
                    }
                    .font(.caption).foregroundStyle(.secondary)
                }
                Divider()
            }
        }
    }
}
