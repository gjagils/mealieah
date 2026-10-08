import SwiftUI

struct SettingsView: View {
    @Environment(Session.self) private var session

    var body: some View {
        NavigationStack {
            Form {
                Section("Server") {
                    Text(session.serverURL)
                    Button("Uitloggen", role: .destructive) { session.logout() }
                }
                Section("Albert Heijn") {
                    Text("De AH-koppeling stel je eenmalig in op de website van de server, onder Instellingen. Daar plak je de inlogcode van ah.nl.")
                        .font(.callout)
                    if let url = URL(string: session.serverURL + "/settings") {
                        Link("Instellingen op de server openen", destination: url)
                    }
                }
            }
            .navigationTitle("Meer")
        }
    }
}
