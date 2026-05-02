const crypto = require('crypto');

module.exports = function plainCssLoader(source) {
  const cssText = JSON.stringify(source.toString());
  const styleId = JSON.stringify(
    `vvault-style-${crypto.createHash('sha1').update(this.resourcePath).digest('hex')}`
  );

  return `
const cssText = ${cssText};
const styleId = ${styleId};

if (typeof document !== 'undefined') {
  let styleTag = document.getElementById(styleId);
  if (!styleTag) {
    styleTag = document.createElement('style');
    styleTag.id = styleId;
    document.head.appendChild(styleTag);
  }

  if (styleTag.textContent !== cssText) {
    styleTag.textContent = cssText;
  }
}

export default cssText;
`;
};
