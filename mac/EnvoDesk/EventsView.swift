import SwiftUI

struct EventsView: View {
    @Binding var selected: EventRow?

    var body: some View {
        Loader(load: { try await $0.events() }) { events, _ in
            HSplitView {
                List(events, selection: Binding(
                    get: { selected.flatMap { s in events.first { $0.id == s.id } } },
                    set: { selected = $0 }
                )) { e in
                    VStack(alignment: .leading, spacing: 2) {
                        Text(e.name).font(.callout.weight(.medium)).lineLimit(2)
                        Text(Fmt.short(e.startsAt, time: false)).font(.caption).foregroundStyle(.secondary)
                    }
                    .tag(e)
                }
                .frame(minWidth: 220, idealWidth: 260, maxWidth: 320)
                Group {
                    if let selected, let e = events.first(where: { $0.id == selected.id }) {
                        EventView(eventId: e.id).id(e.id)
                    } else {
                        ContentUnavailableView("Выбери событие", systemImage: "ticket")
                    }
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
    }
}

struct EventView: View {
    let eventId: Int

    var body: some View {
        Loader(load: { try await $0.event(eventId) }) { card, reload in
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    header(card, reload)
                    pnl(card.pnl)
                    Card(title: "Категории", hint: "цена — по последней продаже, ступени наблюдаются") {
                        categories(card.categories)
                    }
                    HStack(alignment: .top, spacing: 14) {
                        Card(title: "Журнал события") { log(card.log) }
                        Card(title: "Факты и источники", hint: "ручное приоритетнее") { facts(card.facts) }.frame(width: 320)
                    }
                    Card(title: "Заказы", hint: "от последнего к первому") { OrdersList(eventId: eventId) }
                }
                .padding(18)
            }
        }
    }

    private func header(_ c: EventCard, _ reload: @escaping () -> Void) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 12) {
            Text(c.displayName).font(.title2.weight(.semibold))
            Text(Fmt.short(c.startsAt, time: false)).foregroundStyle(.secondary)
            Text(c.daysLeft >= 0 ? "осталось \(c.daysLeft) \(Fmt.plural(c.daysLeft, "день", "дня", "дней"))" : "прошло")
                .font(.caption).padding(.horizontal, 8).padding(.vertical, 2)
                .overlay(Capsule().strokeBorder(.quaternary))
            if !c.hasLetter { Label("нет текста вэлкома", systemImage: "envelope.badge").font(.caption).foregroundStyle(.orange) }
            Spacer()
            Button("Обновить", systemImage: "arrow.clockwise", action: reload).labelStyle(.iconOnly)
        }
    }

    private func pnl(_ p: EventCard.PnL) -> some View {
        HStack(spacing: 10) {
            tile("Вложено", Fmt.money(p.invested), p.invested == 0 ? "закупка не заведена" : "по себестоимости")
            tile("Выручка", Fmt.money(p.revenue), "\(p.ticketsSold) \(Fmt.plural(p.ticketsSold, "билет", "билета", "билетов"))")
            tile("Прибыль", Fmt.money(p.profit), "после сборов, налогов и закупки")
            tile("В остатке", Fmt.money(p.frozen), "по себестоимости")
            tile("Безубыток", p.invested > 0 && p.profit >= 0 ? "Пройден" : p.invested == 0 ? "—" : "Не пройден", "")
        }
    }

    private func tile(_ l: String, _ v: String, _ s: String) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(l.uppercased()).font(.caption2.weight(.semibold)).foregroundStyle(.secondary)
            Text(v).font(.title3.weight(.semibold)).monospacedDigit()
            Text(s).font(.caption).foregroundStyle(.secondary).lineLimit(1)
        }
        .padding(12).frame(maxWidth: .infinity, alignment: .leading)
        .background(.background, in: RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(.quaternary))
    }

    private func categories(_ cats: [EventCard.Category]) -> some View {
        VStack(spacing: 10) {
            if cats.isEmpty { Text("Категории не заведены — билеты без алиасов").foregroundStyle(.secondary) }
            ForEach(cats) { c in
                HStack(alignment: .top, spacing: 16) {
                    VStack(alignment: .leading) {
                        Text(c.name).font(.callout.weight(.semibold))
                        Text("\(c.bought) / \(c.sold) / \(c.left)").font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                        Text("закуп / прод / остаток").font(.caption2).foregroundStyle(.tertiary)
                    }
                    .frame(width: 150, alignment: .leading)
                    VStack(alignment: .leading) {
                        Text(c.currentPrice.map(Fmt.rubles) ?? "—").font(.callout.monospacedDigit())
                        Text(c.cost.map { "с/с \(Fmt.rubles($0))" } ?? "нет себестоимости").font(.caption).foregroundStyle(.secondary)
                        if let m = c.currentMargin { Text("маржа \(Fmt.pct(m))").font(.caption).foregroundStyle(.secondary) }
                    }
                    .frame(width: 150, alignment: .leading)
                    VStack(alignment: .leading, spacing: 3) {
                        ForEach(c.steps) { s in
                            HStack(spacing: 6) {
                                Image(systemName: s.closed ? "checkmark.circle.fill" : "circle").foregroundStyle(s.closed ? .green : .secondary)
                                Text("Ступень \(s.idx) по \(Fmt.rubles(s.price))").font(.caption)
                                Text("\(s.sold) из \(s.quota)").font(.caption.monospacedDigit().weight(.semibold))
                            }
                        }
                        if c.steps.isEmpty { Text("ступени не заведены").font(.caption).foregroundStyle(.secondary) }
                    }
                }
                Divider()
            }
        }
    }

    private func log(_ items: [EventCard.LogItem]) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            if items.isEmpty { Text("Пока ничего").foregroundStyle(.secondary) }
            ForEach(items) { i in
                HStack(spacing: 8) {
                    Text(Fmt.short(i.at)).font(.caption.monospacedDigit()).foregroundStyle(.secondary).frame(width: 80, alignment: .leading)
                    Circle().fill(i.kind == "refund" ? .red : .blue).frame(width: 7, height: 7)
                    Text(i.kind == "refund" ? "Возврат" : "Продажа").font(.callout.weight(.medium))
                    Text("×\(i.tickets) · \(Fmt.rubles(i.total)) · \(i.who ?? "без имени")").font(.callout).foregroundStyle(.secondary).lineLimit(1)
                }
            }
        }
    }

    private func facts(_ facts: [EventCard.Fact]) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            if facts.isEmpty { Text("Фактов нет").foregroundStyle(.secondary) }
            ForEach(facts) { f in
                VStack(alignment: .leading, spacing: 1) {
                    Text(f.field.uppercased()).font(.caption2.weight(.semibold)).foregroundStyle(.secondary)
                    Text(f.value).font(.callout)
                    Text(f.source == "manual" ? "вручную" : f.source).font(.caption2).foregroundStyle(f.source == "manual" ? .teal : .tertiary)
                }
            }
        }
    }
}

/// Бесконечная лента заказов: курсор — время последнего заказа на странице.
struct OrdersList: View {
    @Environment(Session.self) private var session
    let eventId: Int
    @State private var orders: [OrdersPage.Order] = []
    @State private var nextBefore: String?
    @State private var loading = false
    @State private var done = false

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Group {
                    Text("Когда").frame(width: 80, alignment: .leading)
                    Text("Заказ").frame(width: 80, alignment: .leading)
                    Text("Клиент").frame(width: 140, alignment: .leading)
                    Text("Категория").frame(maxWidth: .infinity, alignment: .leading)
                    Text("Шт").frame(width: 30, alignment: .trailing)
                    Text("Сумма").frame(width: 90, alignment: .trailing)
                    Text("Вэлком").frame(width: 80, alignment: .leading)
                    Text("Билеты").frame(width: 90, alignment: .leading)
                }
                .font(.caption2.weight(.semibold)).foregroundStyle(.secondary)
            }
            .padding(.vertical, 4)
            Divider()
            ForEach(orders) { o in
                row(o)
                    .onAppear { if o.id == orders.last?.id { Task { await more() } } }
                Divider()
            }
            if loading { ProgressView().controlSize(.small).padding(8) }
            else if done && orders.isEmpty { Text("Заказов нет").foregroundStyle(.secondary).padding(8) }
            else if done { Text("это все заказы").font(.caption).foregroundStyle(.tertiary).padding(6) }
        }
        .task { await more() }
    }

    private func row(_ o: OrdersPage.Order) -> some View {
        HStack {
            Text(Fmt.short(o.at)).font(.caption.monospacedDigit()).foregroundStyle(.secondary).frame(width: 80, alignment: .leading)
            Text(o.order).font(.caption.monospacedDigit()).frame(width: 80, alignment: .leading)
            Text(o.buyer).font(.callout).lineLimit(1).frame(width: 140, alignment: .leading)
            Text(o.sector).font(.callout).lineLimit(1).foregroundStyle(o.status == "refund" ? .red : .primary).frame(maxWidth: .infinity, alignment: .leading)
            Text("\(o.tickets)").font(.callout.monospacedDigit()).frame(width: 30, alignment: .trailing)
            Text((o.status == "refund" ? "−" : "") + Fmt.rubles(o.total)).font(.callout.monospacedDigit()).frame(width: 90, alignment: .trailing)
            flag(o.welcomeSentAt != nil, "отправлен", "нет").frame(width: 80, alignment: .leading)
            Button {
                Task { await toggleTickets(o) }
            } label: {
                flag(o.ticketsSentAt != nil, o.ticketsSentBy == "outlook" ? "PDF ушёл" : "отправлены", "нет")
            }
            .buttonStyle(.plain).frame(width: 90, alignment: .leading)
            .help("Отметить вручную, если билеты выданы не PDF")
        }
        .padding(.vertical, 5)
    }

    private func flag(_ on: Bool, _ yes: String, _ no: String) -> some View {
        HStack(spacing: 5) {
            Circle().fill(on ? .green : Color.secondary.opacity(0.3)).frame(width: 7, height: 7)
            Text(on ? yes : no).font(.caption).foregroundStyle(on ? .primary : .secondary)
        }
    }

    private func more() async {
        guard !loading, !done else { return }
        loading = true
        defer { loading = false }
        do {
            let page = try await session.api.orders(event: eventId, before: nextBefore)
            orders += page.orders
            nextBefore = page.nextBefore
            done = page.nextBefore == nil
        } catch API.Failure.unauthorized {
            session.expire()
        } catch {
            done = true
        }
    }

    private func toggleTickets(_ o: OrdersPage.Order) async {
        let sent = o.ticketsSentAt == nil
        try? await session.api.ticketsSent(order: o.id, sent)
        if let i = orders.firstIndex(where: { $0.id == o.id }) {
            orders[i] = OrdersPage.Order(id: o.id, order: o.order, at: o.at, status: o.status, channel: o.channel,
                                        total: o.total, tickets: o.tickets, sector: o.sector, buyer: o.buyer,
                                        contactId: o.contactId, welcomeSentAt: o.welcomeSentAt,
                                        ticketsSentAt: sent ? "now" : nil, ticketsSentBy: sent ? "manual" : nil,
                                        letters: o.letters)
        }
    }
}
