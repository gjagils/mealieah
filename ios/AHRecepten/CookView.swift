import SwiftUI
import UIKit

/// Grote letters, scherm blijft aan; tik op een stap of ingrediënt om af te vinken.
struct CookView: View {
    @Environment(\.dismiss) private var dismiss
    let recipe: RecipeDetail
    @State private var checked: Set<Int> = []
    @State private var done: Set<Int> = []

    var body: some View {
        NavigationStack {
            List {
                if recipe.gfMode != "none" {
                    Section {
                        GlutenFreeChip()
                        if !recipe.gfNote.isEmpty { Text(recipe.gfNote) }
                    }
                }
                Section("Ingrediënten") {
                    ForEach(Array(recipe.ingredients.enumerated()), id: \.offset) { index, ingredient in
                        Button {
                            toggle(&checked, index)
                        } label: {
                            HStack(alignment: .top) {
                                Image(systemName: checked.contains(index) ? "checkmark.circle.fill" : "circle")
                                VStack(alignment: .leading) {
                                    Text(ingredient.text)
                                    if ingredient.gluten && recipe.gfMode != "none" && !ingredient.gfSearch.isEmpty {
                                        Text(recipe.gfMode == "replace" ? "i.p.v. gluten: \(ingredient.gfSearch)" : "extra voor 1: \(ingredient.gfSearch)")
                                            .font(.callout).foregroundStyle(.orange)
                                    }
                                }
                            }
                            .font(.title3)
                        }
                        .buttonStyle(.plain)
                    }
                }
                Section("Bereiding") {
                    ForEach(Array(recipe.instructions.enumerated()), id: \.offset) { index, step in
                        Button {
                            toggle(&done, index)
                        } label: {
                            HStack(alignment: .top, spacing: 10) {
                                Text("\(index + 1)").bold()
                                Text(step)
                            }
                            .font(.title3)
                            .opacity(done.contains(index) ? 0.35 : 1)
                        }
                        .buttonStyle(.plain)
                        .padding(.vertical, 4)
                    }
                }
            }
            .navigationTitle(recipe.name)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) { Button("Klaar") { dismiss() } }
            }
        }
        .onAppear { UIApplication.shared.isIdleTimerDisabled = true }
        .onDisappear { UIApplication.shared.isIdleTimerDisabled = false }
    }

    private func toggle(_ set: inout Set<Int>, _ value: Int) {
        if set.contains(value) { set.remove(value) } else { set.insert(value) }
    }
}
