# AH Recepten (iOS)

Native SwiftUI-app (iOS 17+) voor de `ahcommunicator`-backend in deze repo.

## Bouwen

```
brew install xcodegen
cd ios
xcodegen            # maakt AHRecepten.xcodeproj uit project.yml
open AHRecepten.xcodeproj
```

Kies in Xcode je team onder *Signing & Capabilities* en draai op je iPhone.
Bij het eerste starten vul je het serveradres en de pincode in (`APP_PIN` van de backend).

De app praat met de JSON-endpoints in `ahcommunicator/app/api/json_api.py`.
Een server op het thuisnetwerk via `http://` kan (lokaal netwerk is toegestaan); buiten het huis gebruik je `https://`.
