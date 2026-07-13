"""Curated gazetteers for third-party disclosure."""

# Named services / products.
SERVICES = {
    "google analytics", "google adsense", "google adwords", "google ads",
    "google tag manager", "google maps", "google+", "google plus",
    "doubleclick", "admob", "firebase",
    "adobe analytics", "adobe marketing cloud", "omniture", "sitecatalyst",
    "comscore", "scorecardresearch", "quantcast", "nielsen netratings",
    "addthis", "sharethis", "disqus", "gigya", "livefyre",
    "facebook connect", "facebook pixel", "facebook sdk", "like button",
    "optimizely", "crashlytics", "flurry", "mixpanel", "kissmetrics",
    "chartbeat", "new relic", "hotjar", "crazy egg", "clicktale",
    "marketo", "mailchimp", "constant contact", "exacttarget", "responsys",
    "bluekai", "tapad", "rocket fuel", "tubemogul", "openx", "rubicon project",
    "google remarketing", "youtube", "twitter button", "tweet button",
    "google signals", "google ad manager", "firebase analytics", "firebase crashlytics",
    "google fonts", "recaptcha", "google cloud", "bigquery",
    "amazon web services", "aws", "amazon cloudfront", "amazon pinpoint",
    "azure", "microsoft clarity", "app center", "appsflyer", "adjust", "branch",
    "amplitude", "segment", "heap", "fullstory", "logrocket", "sentry",
    "datadog", "cloudflare", "fastly", "akamai", "stripe radar",
    "facebook ads", "meta pixel", "meta audience network", "audience network",
    "tiktok pixel", "tiktok ads", "snap pixel", "pinterest tag",
    "twilio segment", "onesignal", "braze", "iterable", "customer.io",
    "hubspot", "intercom", "zendesk chat", "drift", "hotjar", "vwo",
    "the trade desk", "index exchange", "pubmatic", "magnite", "liveintent",
    "integral ad science", "doubleverify", "moat",
    "tapjoy", "applovin", "unity ads", "vungle", "chartboost", "inmobi",
    "mopub", "ironsource", "adcolony", "giphy",
    "google sign-in", "facebook login", "apple sign in",
    "mandrill", "sendinblue",
}

# Named companies / organisations.
COMPANIES = {
    "google", "facebook", "twitter", "microsoft", "apple", "amazon",
    "yahoo", "yahoo!", "linkedin", "adobe", "pinterest", "instagram",
    "paypal", "oracle", "salesforce", "ebay", "aol", "verizon", "at&t",
    "comcast", "bing", "snapchat", "tumblr", "reddit", "vimeo", "spotify",
    "nielsen", "acxiom", "experian", "equifax", "transunion", "epsilon",
    "datalogix", "liveramp", "neustar", "bluekai",  # also appears as service
    "criteo", "taboola", "outbrain", "appnexus", "mediamath", "turn",
    "stripe", "braintree", "visa", "mastercard", "american express",
    "wordpress", "shopify", "zendesk", "intercom", "segment", "adyen",
    "meta", "meta platforms", "alphabet", "tiktok", "bytedance", "snap",
    "twilio", "sendgrid", "mailgun",
    "okta", "auth0", "atlassian", "pagerduty",
    "klaviyo", "magnite", "id5", "lotame", "throtle", "infosum",
    "snowflake",
    # data brokers / people-search
    "corelogic", "spokeo", "whitepages", "intelius", "beenverified",
    "peoplefinders", "lexisnexis", "thomson reuters", "kochava",
    # consent-management platforms
    "onetrust", "trustarc", "cookiebot", "usercentrics", "didomi", "quantcast",
    "sourcepoint", "osano", "termly", "iubenda",
    "vkontakte",
}

# Words which, as a tail, transform a company into a service.
SERVICE_TAIL_WORDS = {
    "analytics", "ads", "adsense", "adwords", "pixel", "sdk", "api",
    "connect", "platform", "cloud", "network", "beacon", "tag", "manager",
    "insights", "audience", "audiences", "remarketing", "metrics", "studio",
}
