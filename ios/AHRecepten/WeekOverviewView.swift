import SwiftUI

/// Gezinsweergave: wat staat er vandaag en deze week op het menu.
struct WeekOverviewView: View {
    @Environment(Session.self) private var session
    @State private var week: WeekResponse?
    @State private var errorText: String?

    var body: some View {
        NavigationStack {
            List {
                if let week {
                    ForEach(week.days) { day in
                        Section {
                            if day.recipes.isEmpty {
                                Text("Niets gepland").foregroundStyle(.secondary)
                            }
                            ForEach(Array(day.recipes.enumerated()), id: \.offset) { _, recipe in
                                NavigationLink(value: recipe) { RecipeRow(recipe: recipe) }
                            }
                        } header: {
                            Text(day.today ? "\(day.label) · vandaag" : day.label)
                                .fontWeight(day.today ? .bold : .regular)
                        }
                    }
                } else if let errorText {
                    Text(errorText).foregroundStyle(.red)
                } else {
                    ProgressView()
                }
            }
            .navigationTitle("Deze week")
            .navigationDestination(for: RecipeSummary.self) { RecipeDetailView(recipeID: $0.id) }
            .refreshable { await load() }
            .task { await load() }
        }
    }

    @MainActor
    private func load() async {
        guard let api = session.api else { return }
        do {
            let result: WeekResponse = try await api.get("api/week")
            week = result
            errorText = nil
        } catch {
            errorText = error.localizedDescription
        }
    }
}
