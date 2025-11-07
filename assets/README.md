# VVAULT Cinematic Login Background Images

This directory contains the cinematic background images for the VVAULT login experience.

## Background Images

### vvault_sunset.png
- **Theme**: Ceremony. Recognition of Arrival.
- **Use Case**: First-time device (unrecognized IP/signal)
- **Visual**: Observatory at dusk with warm sunset colors
- **Dimensions**: 1920x1080px

### vvault_sunrise.png  
- **Theme**: Familiarity. Belonging. Trusted Return.
- **Use Case**: Returning user (recognized IP/trusted signal)
- **Visual**: Snowy pine landscape with sunrise
- **Dimensions**: 1920x1080px

## Usage

The CinematicLogin component automatically selects the appropriate background based on the `isTrustedDevice` state:

```javascript
const backgroundImage = isTrustedDevice ? 'vvault_sunrise.png' : 'vvault_sunset.png';
```

## Image Requirements

- **Format**: PNG with transparency support
- **Resolution**: 1920x1080 (Full HD)
- **Optimization**: Compressed for web delivery
- **Accessibility**: High contrast for text overlay readability

## Customization

To replace these images:
1. Create new images with the same dimensions
2. Maintain the same filenames or update the component
3. Ensure proper contrast for text overlay
4. Test on various screen sizes
