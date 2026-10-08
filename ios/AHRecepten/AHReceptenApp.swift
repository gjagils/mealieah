import SwiftUI

@main
struct AHReceptenApp: App {
    @State private var session = Session()

    var body: some Scene {
        WindowGroup {
            Group {
                if session.isConnected {
                    RootView()
                } else {
                    ConnectView()
                }
            }
            .environment(session)
        }
    }
}

struct RootView: View {
    var body: some View {
        TabView {
            WeekOverviewView().tabItem { Label("Vandaag", systemImage: "calendar") }
            RecipesView().tabItem { Label("Recepten", systemImage: "book") }
            PlanView().tabItem { Label("Weekmenu", systemImage: "list.bullet.rectangle") }
            AllerhandeView().tabItem { Label("AH", systemImage: "magnifyingglass") }
            SettingsView().tabItem { Label("Meer", systemImage: "gearshape") }
        }
    }
}
