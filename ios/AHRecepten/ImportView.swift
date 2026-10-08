import PhotosUI
import SwiftUI

/// Nieuw recept uit een link, geplakte tekst of foto's.
struct ImportView: View {
    @Environment(Session.self) private var session
    @Environment(\.dismiss) private var dismiss
    @State private var url = ""
    @State private var text = ""
    @State private var photos: [PhotosPickerItem] = []
    @State private var busy = false
    @State private var errorText: String?

    var body: some View {
        NavigationStack {
            Form {
                Section("Link naar een recept") {
                    TextField("https://...", text: $url)
                        .keyboardType(.URL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                }
                Section("of plak de tekst") {
                    TextEditor(text: $text).frame(minHeight: 100)
                }
                Section("of kies foto's (bijv. voor- en achterkant)") {
                    PhotosPicker(selection: $photos, maxSelectionCount: 6, matching: .images) {
                        Label(photos.isEmpty ? "Foto's kiezen" : "\(photos.count) foto's gekozen", systemImage: "photo.on.rectangle")
                    }
                }
                if let errorText {
                    Section { Text(errorText).foregroundStyle(.red) }
                }
                Section {
                    Button {
                        Task { await runImport() }
                    } label: {
                        if busy {
                            HStack { ProgressView(); Text("Bezig met omzetten...") }
                        } else {
                            Text("Recept omzetten")
                        }
                    }
                    .disabled(busy || (url.isEmpty && text.isEmpty && photos.isEmpty))
                }
            }
            .navigationTitle("Nieuw recept")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Annuleer") { dismiss() } }
            }
        }
    }

    @MainActor
    private func runImport() async {
        guard let api = session.api else { return }
        busy = true
        errorText = nil
        defer { busy = false }
        var images: [Data] = []
        for item in photos {
            if let data = try? await item.loadTransferable(type: Data.self),
               let jpeg = UIImage(data: data)?.jpegData(compressionQuality: 0.85) {
                images.append(jpeg)
            }
        }
        do {
            let result = try await api.importRecipe(url: url, text: text, images: images)
            if result.ok { dismiss() } else { errorText = result.error ?? "Importeren mislukt" }
        } catch {
            errorText = error.localizedDescription
        }
    }
}
