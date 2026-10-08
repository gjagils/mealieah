import SwiftUI

struct RecipeDetailView: View {
    @Environment(Session.self) private var session
    let recipeID: Int
    @State private var recipe: RecipeDetail?
    @State private var showCook = false
    @State private var busy = false
    @State private var message: String?
    @State private var errorText: String?

    var body: some View {
        List {
            if let recipe {
                Section {
                    HStack {
                        Spacer()
                        RecipeImage(path: recipe.imageUrl, size: 180)
                        Spacer()
                    }
                    .listRowBackground(Color.clear)
                    let meta = [recipe.servings, recipe.totalTime].filter { !$0.isEmpty }.joined(separator: " · ")
                    if !meta.isEmpty { Text(meta).foregroundStyle(.secondary) }
                    if !recipe.description.isEmpty { Text(recipe.description) }
                    Button { showCook = true } label: { Label("Kookmodus", systemImage: "flame") }
                        .buttonStyle(.borderedProminent)
                }

                Section("Glutenvrij voor minstens 1 persoon") {
                    switch recipe.gfMode {
                    case "extra": Text("Extra glutenvrij product erbij (voor 1 persoon)")
                    case "replace": Text("Ingrediënt voor iedereen vervangen")
                    default: Text("Nog niet ingesteld").foregroundStyle(.secondary)
                    }
                    if !recipe.gfNote.isEmpty { Text(recipe.gfNote).font(.callout) }
                    Button {
                        Task { await suggestGlutenFree() }
                    } label: {
                        if busy { ProgressView() } else { Label("Voorstel van Claude", systemImage: "wand.and.stars") }
                    }
                    .disabled(busy)
                    if let message { Text(message).font(.caption).foregroundStyle(.secondary) }
                }

                Section("Ingrediënten") {
                    ForEach(Array(recipe.ingredients.enumerated()), id: \.offset) { _, ingredient in
                        VStack(alignment: .leading, spacing: 2) {
                            Text(ingredient.text).opacity(ingredient.skip ? 0.5 : 1)
                            if ingredient.gluten {
                                Text("Bevat gluten → \(ingredient.gfSearch)\(ingredient.gfProduct.map { " (\($0))" } ?? "")")
                                    .font(.caption).foregroundStyle(.orange)
                            }
                        }
                    }
                }

                Section("Bereiding") {
                    ForEach(Array(recipe.instructions.enumerated()), id: \.offset) { index, step in
                        HStack(alignment: .top, spacing: 8) {
                            Text("\(index + 1).").bold()
                            Text(step)
                        }
                    }
                }

                if let url = URL(string: recipe.sourceUrl), !recipe.sourceUrl.isEmpty {
                    Section { Link("Bron openen", destination: url) }
                }
            } else if let errorText {
                Text(errorText).foregroundStyle(.red)
            } else {
                ProgressView()
            }
        }
        .navigationTitle(recipe?.name ?? "Recept")
        .navigationBarTitleDisplayMode(.inline)
        .fullScreenCover(isPresented: $showCook) {
            if let recipe { CookView(recipe: recipe) }
        }
        .task { await load() }
    }

    @MainActor
    private func load() async {
        guard let api = session.api else { return }
        do {
            let result: RecipeDetail = try await api.get("api/recipes/\(recipeID)")
            recipe = result
            errorText = nil
        } catch {
            errorText = error.localizedDescription
        }
    }

    @MainActor
    private func suggestGlutenFree() async {
        guard let api = session.api else { return }
        busy = true
        message = nil
        defer { busy = false }
        do {
            let _: GlutenSuggestResult = try await api.post("api/recipe/\(recipeID)/gluten-suggest")
            await load()
            message = "Voorstel opgeslagen."
        } catch {
            message = error.localizedDescription
        }
    }
}
