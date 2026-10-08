import SwiftUI

/// Weekmenu plannen, vastzetten en controleren of alles op het AH-lijstje staat.
struct PlanView: View {
    @Environment(Session.self) private var session
    @State private var week: WeekResponse?
    @State private var allRecipes: [RecipeSummary] = []
    @State private var pickerDay: String?
    @State private var busy = false
    @State private var message: String?
    @State private var errorText: String?

    var body: some View {
        NavigationStack {
            List {
                if let week {
                    Section {
                        HStack {
                            Button { Task { await load(week: week.prevWeek) } } label: { Image(systemName: "chevron.left") }
                            Spacer()
                            Text("Week van \(week.week)").bold()
                            Spacer()
                            Button { Task { await load(week: week.nextWeek) } } label: { Image(systemName: "chevron.right") }
                        }
                        .buttonStyle(.borderless)
                    }

                    ForEach(week.days) { day in
                        Section(day.label) {
                            ForEach(Array(day.recipes.enumerated()), id: \.offset) { _, recipe in
                                RecipeRow(recipe: recipe)
                            }
                            .onDelete { offsets in
                                Task { await remove(from: day, at: offsets) }
                            }
                            Button { pickerDay = day.date } label: { Label("Recept toevoegen", systemImage: "plus") }
                        }
                    }

                    statusSection(week.status)
                } else if let errorText {
                    Text(errorText).foregroundStyle(.red)
                } else {
                    ProgressView()
                }
            }
            .navigationTitle("Weekmenu")
            .refreshable { await load(week: week?.week) }
            .task { await load(week: nil) }
            .sheet(item: Binding(
                get: { pickerDay.map { DayID(date: $0) } },
                set: { pickerDay = $0?.date }
            )) { day in
                RecipePicker(recipes: allRecipes) { recipe in
                    Task { await add(recipe, on: day.date) }
                }
            }
        }
    }

    @ViewBuilder
    private func statusSection(_ status: WeekStatus) -> some View {
        Section("Boodschappen") {
            if status.needed == 0 && status.unmatched.isEmpty {
                Text("Nog niets gepland.").foregroundStyle(.secondary)
            } else if status.complete {
                Label("Alle \(status.needed) producten staan op je AH-lijstje", systemImage: "checkmark.circle.fill")
                    .foregroundStyle(.green)
            } else {
                if !status.missing.isEmpty {
                    Label("Nog \(status.missing.count) producten niet op je lijstje", systemImage: "exclamationmark.triangle")
                        .foregroundStyle(.orange)
                    ForEach(status.missing) { item in Text("\(item.quantity)× \(item.name)").font(.callout) }
                }
                if !status.unmatched.isEmpty {
                    Label("Geen AH-product gekoppeld", systemImage: "questionmark.circle").foregroundStyle(.orange)
                    ForEach(status.unmatched, id: \.self) { Text($0).font(.callout) }
                }
            }
            if status.locked { Label("Weekmenu is vastgezet", systemImage: "lock.fill") }
            Button {
                Task { await sync(locked: !status.locked) }
            } label: {
                Text(status.locked ? "Weekmenu ontgrendelen" : "Weekmenu vastzetten")
            }
            .disabled(busy)
            Button { Task { await sync(locked: nil) } } label: { Text("Controleer en vul aan") }
                .disabled(busy)
            if busy { ProgressView() }
            if let message { Text(message).font(.caption).foregroundStyle(.secondary) }
        }
    }

    private struct DayID: Identifiable { let date: String; var id: String { date } }

    @MainActor
    private func load(week weekStart: String?) async {
        guard let api = session.api else { return }
        do {
            let query = weekStart.map { [URLQueryItem(name: "week", value: $0)] } ?? []
            let result: WeekResponse = try await api.get("api/week", query: query)
            week = result
            let list: RecipesResponse = try await api.get("api/recipes")
            allRecipes = list.recipes
            errorText = nil
        } catch {
            errorText = error.localizedDescription
        }
    }

    private func planDict(_ week: WeekResponse) -> [String: [Int]] {
        Dictionary(uniqueKeysWithValues: week.days.map { ($0.date, $0.recipes.map(\.id)) })
    }

    @MainActor
    private func save(_ days: [String: [Int]], week current: WeekResponse) async {
        guard let api = session.api else { return }
        do {
            let _: SavePlanResult = try await api.post("api/plan", json: SavePlanBody(week: current.week, days: days))
            await load(week: current.week)
        } catch {
            message = error.localizedDescription
        }
    }

    @MainActor
    private func add(_ recipe: RecipeSummary, on date: String) async {
        guard let week else { return }
        var days = planDict(week)
        days[date, default: []].append(recipe.id)
        await save(days, week: week)
    }

    @MainActor
    private func remove(from day: PlanDay, at offsets: IndexSet) async {
        guard let week else { return }
        var days = planDict(week)
        days[day.date]?.remove(atOffsets: offsets)
        await save(days, week: week)
    }

    @MainActor
    private func sync(locked: Bool?) async {
        guard let api = session.api, let current = week else { return }
        busy = true
        message = nil
        defer { busy = false }
        do {
            let result: SyncResult = try await api.post("api/plan/sync", json: SyncBody(week: current.week, locked: locked))
            if result.ok {
                message = (result.added ?? 0) > 0 ? "\(result.added ?? 0) producten toegevoegd aan je AH-lijstje." : nil
                await load(week: current.week)
            } else {
                message = result.error ?? "Mislukt"
            }
        } catch {
            message = error.localizedDescription
        }
    }
}

struct RecipePicker: View {
    @Environment(\.dismiss) private var dismiss
    let recipes: [RecipeSummary]
    let onPick: (RecipeSummary) -> Void
    @State private var search = ""

    private var filtered: [RecipeSummary] {
        search.isEmpty ? recipes : recipes.filter { $0.name.localizedCaseInsensitiveContains(search) }
    }

    var body: some View {
        NavigationStack {
            List(filtered) { recipe in
                Button {
                    onPick(recipe)
                    dismiss()
                } label: { RecipeRow(recipe: recipe) }
                .buttonStyle(.plain)
            }
            .searchable(text: $search, prompt: "Zoek recept")
            .navigationTitle("Kies een recept")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Annuleer") { dismiss() } }
            }
        }
    }
}
