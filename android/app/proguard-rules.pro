# Add project specific ProGuard rules here.
-keep class com.htmltoapk.main.** { *; }
-keepclassmembers class * {
    @android.annotation.JavascriptInterface <methods>;
}
