package cn.researchwiki.collector;

import android.graphics.Canvas;
import android.graphics.ColorFilter;
import android.graphics.Paint;
import android.graphics.PixelFormat;
import android.graphics.RectF;
import android.graphics.drawable.Drawable;

/** A paper-colored face above a crisp offset base, with a physical pressed state. */
final class ComicSurface extends Drawable {
    private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final float density;
    private final int face;
    private final int ink;
    private boolean pressed;
    private boolean focused;

    ComicSurface(float density, int face, int ink) {
        this.density = density;
        this.face = face;
        this.ink = ink;
    }

    @Override public boolean isStateful() { return true; }

    @Override protected boolean onStateChange(int[] states) {
        boolean nextPressed = false;
        boolean nextFocused = false;
        for (int state : states) {
            nextPressed |= state == android.R.attr.state_pressed;
            nextFocused |= state == android.R.attr.state_focused;
        }
        if (pressed == nextPressed && focused == nextFocused) return false;
        pressed = nextPressed;
        focused = nextFocused;
        invalidateSelf();
        return true;
    }

    @Override public void draw(Canvas canvas) {
        float stroke = (focused ? 3 : 2) * density;
        float offset = 4 * density;
        float radius = 12 * density;
        RectF rect = new RectF(getBounds());
        rect.inset(stroke / 2, stroke / 2);
        rect.right -= offset;
        rect.bottom -= offset;
        RectF base = new RectF(rect);
        base.offset(offset, offset);
        paint.setStyle(Paint.Style.FILL);
        paint.setColor(ink);
        canvas.drawRoundRect(base, radius, radius, paint);
        if (pressed) rect.offset(offset, offset);
        paint.setColor(face);
        canvas.drawRoundRect(rect, radius, radius, paint);
        paint.setStyle(Paint.Style.STROKE);
        paint.setColor(ink);
        paint.setStrokeWidth(stroke);
        canvas.drawRoundRect(rect, radius, radius, paint);
    }

    @Override public void setAlpha(int alpha) { paint.setAlpha(alpha); invalidateSelf(); }
    @Override public void setColorFilter(ColorFilter filter) { paint.setColorFilter(filter); invalidateSelf(); }
    @Override public int getOpacity() { return PixelFormat.TRANSLUCENT; }
}
