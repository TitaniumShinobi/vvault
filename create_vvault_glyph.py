#!/usr/bin/env python3
"""
Create VVAULT Glyph Image
Generate a simple VVAULT logo/glyph for the desktop login screen.
"""

from PIL import Image, ImageDraw, ImageFont
import os

def create_vvault_glyph():
    """Create a VVAULT glyph image"""
    # Create a 300x300 image with transparent background
    size = (300, 300)
    img = Image.new('RGBA', size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    
    # Define colors
    blue = (59, 130, 246, 255)  # #3b82f6
    purple = (139, 92, 246, 255)  # #8b5cf6
    cyan = (6, 182, 212, 255)  # #06b6d4
    white = (255, 255, 255, 255)
    
    # Draw background circle with gradient effect
    center = (150, 150)
    radius = 120
    
    # Draw multiple circles for gradient effect
    for i in range(radius, 0, -5):
        alpha = int(255 * (1 - i / radius) * 0.3)
        color = (59, 130, 246, alpha)
        draw.ellipse([center[0] - i, center[1] - i, center[0] + i, center[1] + i], 
                    fill=color, outline=None)
    
    # Draw the main "V" shape
    # VVAULT "V" - large and centered
    v_points = [
        (100, 80),   # Top left
        (150, 180),  # Bottom center
        (200, 80)    # Top right
    ]
    
    # Draw the V with gradient effect
    for i in range(8, 0, -1):
        offset = i * 2
        color_intensity = 255 - (i * 20)
        v_color = (59, 130, 246, color_intensity)
        
        adjusted_points = [
            (v_points[0][0] + offset, v_points[0][1] + offset),
            (v_points[1][0], v_points[1][1] - offset),
            (v_points[2][0] - offset, v_points[2][1] + offset)
        ]
        
        draw.polygon(adjusted_points, fill=v_color, outline=None)
    
    # Draw security shield outline
    shield_points = [
        (150, 100),  # Top
        (180, 130),  # Right
        (180, 160),  # Right bottom
        (150, 190),  # Bottom center
        (120, 160),  # Left bottom
        (120, 130)   # Left
    ]
    
    # Draw shield with transparency
    shield_color = (59, 130, 246, 100)
    draw.polygon(shield_points, fill=shield_color, outline=blue, width=2)
    
    # Add terminal-style dots
    dot_positions = [
        (80, 80), (220, 80),   # Top corners
        (80, 220), (220, 220)   # Bottom corners
    ]
    
    for pos in dot_positions:
        # Draw glowing dots
        for i in range(8, 0, -1):
            dot_radius = i * 2
            dot_alpha = int(255 * (1 - i / 8))
            dot_color = (59, 130, 246, dot_alpha)
            draw.ellipse([pos[0] - dot_radius, pos[1] - dot_radius, 
                         pos[0] + dot_radius, pos[1] + dot_radius], 
                        fill=dot_color, outline=None)
    
    # Add "VVAULT" text at the bottom
    try:
        # Try to use a system font
        font = ImageFont.truetype("/System/Library/Fonts/Arial.ttf", 24)
    except:
        try:
            font = ImageFont.truetype("arial.ttf", 24)
        except:
            try:
                font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 24)
            except:
                font = ImageFont.load_default()
    
    text = "VVAULT"
    text_bbox = draw.textbbox((0, 0), text, font=font)
    text_width = text_bbox[2] - text_bbox[0]
    text_height = text_bbox[3] - text_bbox[1]
    
    text_x = (size[0] - text_width) // 2
    text_y = 220
    
    # Draw text with glow effect
    for i in range(3, 0, -1):
        glow_color = (59, 130, 246, 50)
        draw.text((text_x + i, text_y + i), text, font=font, fill=glow_color)
    
    # Draw main text
    draw.text((text_x, text_y), text, font=font, fill=white)
    
    return img

def main():
    """Main function"""
    print("🎨 Creating VVAULT Glyph...")
    
    # Create assets directory
    assets_dir = "/Users/devonwoodson/Documents/GitHub/VVAULT/assets"
    os.makedirs(assets_dir, exist_ok=True)
    
    # Create the glyph
    glyph = create_vvault_glyph()
    
    # Save as PNG
    output_path = os.path.join(assets_dir, "vvault_glyph.png")
    glyph.save(output_path, "PNG")
    
    print(f"✅ VVAULT glyph created: {output_path}")
    print(f"   Size: {glyph.size}")
    print(f"   Format: PNG with transparency")
    
    # Also create a smaller version for the login screen
    small_glyph = glyph.resize((150, 150), Image.Resampling.LANCZOS)
    small_output_path = os.path.join(assets_dir, "vvault_glyph_small.png")
    small_glyph.save(small_output_path, "PNG")
    
    print(f"✅ Small VVAULT glyph created: {small_output_path}")

if __name__ == "__main__":
    main()
