import SwiftUI

struct RecipeImage: View {
    @Environment(Session.self) private var session
    let path: String
    var size: CGFloat = 56

    var body: some View {
        Group {
            if let url = session.api?.imageURL(path) {
                AsyncImage(url: url) { phase in
                    if let image = phase.image {
                        image.resizable().scaledToFill()
                    } else {
                        Color(.systemGray5)
                    }
                }
            } else {
                Color(.systemGray5)
            }
        }
        .frame(width: size, height: size)
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}

struct GlutenFreeChip: View {
    var body: some View {
        Text("glutenvrij")
            .font(.caption2)
            .padding(.horizontal, 6)
            .padding(.vertical, 2)
            .background(Color.green.opacity(0.15), in: Capsule())
            .foregroundStyle(.green)
    }
}

struct RecipeRow: View {
    let recipe: RecipeSummary

    var body: some View {
        HStack(spacing: 12) {
            RecipeImage(path: recipe.imageUrl)
            VStack(alignment: .leading, spacing: 2) {
                Text(recipe.name).font(.body)
                let meta = [recipe.servings, recipe.totalTime].filter { !$0.isEmpty }.joined(separator: " · ")
                if !meta.isEmpty {
                    Text(meta).font(.caption).foregroundStyle(.secondary)
                }
            }
            Spacer()
            if recipe.gfMode != "none" { GlutenFreeChip() }
        }
    }
}
