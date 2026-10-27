"""English / Hindi text for the pages finders see. Add more keys here to translate more pages."""
from flask import request, session

from config import LANGS

TRANSLATIONS = {
    "en": {
        "nav_community": "Community animals", "nav_login": "Log in", "nav_signup": "Sign up",
        "nav_dashboard": "My pets", "nav_logout": "Log out",
        "hero_title": "An ID card for your pet 🐶🐱",
        "hero_sub": "Register your pet, print the QR tag, and attach it to the collar. "
                    "If your pet gets lost, anyone can scan the tag and help it get home.",
        "hero_btn": "Get started", "how_title": "How it works",
        "how1": "Create a free account and add your pet's details.",
        "how2": "Download the printable QR tag as a PDF.",
        "how3": "Print it, laminate it, and attach it to the collar.",
        "how4": "A finder scans the QR and sees your pet's details, and you get an email and SMS instantly.",
        "lost_banner": "⚠️ This pet is LOST! Please help it get home. Contact the owner below.",
        "community_badge": "Community animal", "cared_by": "Looked after by",
        "species": "Species", "gender": "Gender", "color": "Color", "age": "Age",
        "birth_month": "Birth month", "temperament": "Temperament", "home_area": "Home area",
        "microchip": "Microchip", "sterilized": "Sterilized",
        "where_i_live": "📍 Where it lives (approximate area)",
        "open_in_maps": "Open in Google Maps ↗",
        "vaccinations": "💉 Vaccinations", "next_due": "Next due", "vaccine": "Vaccine",
        "given_on": "Given on", "next_due_col": "Next due",
        "no_vaccinations": "No vaccinations recorded.",
        "verified": "✔ Verified by", "medical_notes": "Medical notes",
        "food_notes": "Food notes", "vet": "Vet",
        "found_me": "Found this pet? Contact the owner",
        "call": "📞 Call", "or_message": "Or leave a message below.",
        "owner_private": "The owner's number is private. Leave your details below and the owner is "
                         "alerted by email and SMS right away.",
        "your_name": "Your name", "your_phone": "Your phone",
        "where_found": "Where did you find it?", "street_landmark": "Street / landmark",
        "message": "Message", "message_ph": "I found your pet near...",
        "share_location": "📍 Share my exact location", "location_added": "Location added ✓",
        "location_denied": "Couldn't get location (allow location access, and note it needs HTTPS).",
        "send_to_owner": "Send to owner", "captcha_label": "Quick check: what is",
        "overdue": "Overdue",
    },
    "hi": {
        "nav_community": "समुदाय के पशु", "nav_login": "लॉग इन", "nav_signup": "साइन अप",
        "nav_dashboard": "मेरे पालतू", "nav_logout": "लॉग आउट",
        "hero_title": "आपके पालतू के लिए एक पहचान पत्र 🐶🐱",
        "hero_sub": "अपने पालतू को रजिस्टर करें, QR टैग प्रिंट करें और उसे कॉलर पर लगाएँ। "
                    "पालतू खो जाए तो कोई भी टैग स्कैन करके उसे घर पहुँचाने में मदद कर सकता है।",
        "hero_btn": "शुरू करें", "how_title": "यह कैसे काम करता है",
        "how1": "मुफ़्त अकाउंट बनाएँ और अपने पालतू की जानकारी जोड़ें।",
        "how2": "प्रिंट करने योग्य QR टैग PDF में डाउनलोड करें।",
        "how3": "इसे प्रिंट करें, लैमिनेट करें और कॉलर पर लगाएँ।",
        "how4": "कोई भी QR स्कैन करके जानकारी देख सकता है, और आपको तुरंत ईमेल और SMS मिल जाता है।",
        "lost_banner": "⚠️ यह पालतू खो गया है! कृपया इसे घर पहुँचाने में मदद करें। नीचे मालिक से संपर्क करें।",
        "community_badge": "समुदाय का पशु", "cared_by": "देखभाल करने वाले",
        "species": "प्रजाति", "gender": "लिंग", "color": "रंग", "age": "उम्र",
        "birth_month": "जन्म का महीना", "temperament": "स्वभाव", "home_area": "घर का इलाका",
        "microchip": "माइक्रोचिप", "sterilized": "नसबंदी",
        "where_i_live": "📍 यह कहाँ रहता है (लगभग इलाका)",
        "open_in_maps": "Google Maps में खोलें ↗",
        "vaccinations": "💉 टीकाकरण", "next_due": "अगला टीका", "vaccine": "टीका",
        "given_on": "लगाने की तारीख", "next_due_col": "अगली तारीख",
        "no_vaccinations": "कोई टीकाकरण दर्ज नहीं है।",
        "verified": "✔ सत्यापित:", "medical_notes": "स्वास्थ्य संबंधी नोट",
        "food_notes": "खान-पान संबंधी नोट", "vet": "पशु-चिकित्सक",
        "found_me": "यह पालतू मिला? मालिक से संपर्क करें",
        "call": "📞 कॉल करें:", "or_message": "या नीचे संदेश छोड़ें।",
        "owner_private": "मालिक का नंबर निजी है। नीचे अपनी जानकारी भरें, मालिक को तुरंत ईमेल और SMS से सूचना मिल जाएगी।",
        "your_name": "आपका नाम", "your_phone": "आपका फ़ोन नंबर",
        "where_found": "यह आपको कहाँ मिला?", "street_landmark": "गली / पहचान की जगह",
        "message": "संदेश", "message_ph": "मुझे आपका पालतू यहाँ मिला...",
        "share_location": "📍 मेरी सटीक लोकेशन साझा करें", "location_added": "लोकेशन जुड़ गई ✓",
        "location_denied": "लोकेशन नहीं मिल सकी (लोकेशन की अनुमति दें; इसके लिए HTTPS चाहिए)।",
        "send_to_owner": "मालिक को भेजें", "captcha_label": "सुरक्षा जाँच: कितना होता है",
        "overdue": "देरी",
    },
}


def current_lang():
    lang = session.get("lang")
    if lang in LANGS:
        return lang
    return request.accept_languages.best_match(list(LANGS)) or "en"


def t(key):
    return TRANSLATIONS.get(current_lang(), {}).get(key) or TRANSLATIONS["en"].get(key, key)
