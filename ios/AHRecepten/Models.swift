import Foundation

struct RecipeSummary: Decodable, Identifiable, Hashable {
    let id: Int
    let name: String
    let servings: String
    let totalTime: String
    let imageUrl: String
    let gfMode: String
}

struct RecipesResponse: Decodable { let recipes: [RecipeSummary] }

struct Ingredient: Decodable, Identifiable {
    var id: String { text }
    let text: String
    let skip: Bool
    let gluten: Bool
    let gfSearch: String
    let product: String?
    let gfProduct: String?
}

struct RecipeDetail: Decodable {
    let id: Int
    let name: String
    let servings: String
    let totalTime: String
    let imageUrl: String
    let gfMode: String
    let gfNote: String
    let description: String
    let sourceUrl: String
    let instructions: [String]
    let ingredients: [Ingredient]
}

struct MissingItem: Decodable, Identifiable {
    var id: String { name }
    let name: String
    let quantity: Int
}

struct WeekStatus: Decodable {
    let needed: Int
    let missing: [MissingItem]
    let unmatched: [String]
    let complete: Bool
    let locked: Bool
}

struct PlanDay: Decodable, Identifiable {
    var id: String { date }
    let date: String
    let label: String
    let today: Bool
    let recipes: [RecipeSummary]
}

struct WeekResponse: Decodable {
    let week: String
    let prevWeek: String
    let nextWeek: String
    let days: [PlanDay]
    let status: WeekStatus
}

struct SavePlanBody: Encodable {
    let week: String
    let days: [String: [Int]]
}

struct SavePlanResult: Decodable { let ok: Bool }

struct SyncBody: Encodable {
    let week: String
    let locked: Bool?
}

struct SyncResult: Decodable {
    let ok: Bool
    let added: Int?
    let error: String?
    let status: WeekStatus?
}

struct ImportResult: Decodable {
    let ok: Bool
    let id: Int?
    let error: String?
}

struct AHRecipeHit: Decodable, Identifiable {
    let id: Int
    let title: String
    let servings: String
    let url: String
    var saved: Bool
}

struct AHSearchResponse: Decodable { let results: [AHRecipeHit] }

struct AddResult: Decodable {
    let ok: Bool
    let id: Int?
    let error: String?
}

struct GlutenSuggestResult: Decodable {
    let ok: Bool
    let error: String?
}
