import SwiftUI

struct RecipesView: View {
    @Environment(Session.self) private var session
    @State private var recipes: [RecipeSummary] = []
    @State private var search = ""
    @State private var showImport = false
    @State private var errorText: String?

    private var filtered: [RecipeSummary] {
        search.isEmpty ? recipes : recipes.filter { $0.name.localizedCaseInsensitiveContains(search) }
    }

    var body: some View {
        NavigationStack {
            List {
                if let errorText {
                    Text(errorText).foregroundStyle(.red)
                }
                ForEach(filtered) { recipe in
                    NavigationLink(value: recipe) { RecipeRow(recipe: recipe) }
                }
            }
            .searchable(text: $search, prompt: "Zoek recept")
            .navigationTitle("Recepten")
            .navigationDestination(for: RecipeSummary.self) { RecipeDetailView(recipeID: $0.id) }
            .toolbar {
                ToolbarItem(placement: .primaryAction) {
                    Button { showImport = true } label: { Image(systemName: "plus") }
                }
            }
            .sheet(isPresented: $showImport, onDismiss: { Task { await load() } }) { ImportView() }
            .refreshable { await load() }
            .task { await load() }
        }
    }

    @MainActor
    private func load() async {
        guard let api = session.api else { return }
        do {
            let result: RecipesResponse = try await api.get("api/recipes")
            recipes = result.recipes
            errorText = nil
        } catch {
            errorText = error.localizedDescription
        }
    }
}
