import { motion, type HTMLMotionProps } from 'motion/react'

/**
 * Spring physics for panels: a card settles into place instead of appearing. MotionConfig in
 * the shell sets the spring and, under Calm mode or reduced motion, turns it off so this is
 * a plain div.
 */
export function Panel({ delay = 0, ...props }: HTMLMotionProps<'div'> & { delay?: number }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 10, scale: 0.985 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{ type: 'spring', stiffness: 220, damping: 26, mass: 0.9, delay }}
      {...props}
    />
  )
}

/** A value that changed: the new figure springs up into place, the old one drops away. */
export function SpringValue({ children, k, className }: { children: React.ReactNode; k: string | number; className?: string }) {
  return (
    <span className={className} style={{ display: 'inline-grid' }}>
      <motion.span
        key={k}
        style={{ gridArea: '1 / 1' }}
        initial={{ opacity: 0, y: 8, filter: 'blur(2px)' }}
        animate={{ opacity: 1, y: 0, filter: 'blur(0px)' }}
        transition={{ type: 'spring', stiffness: 320, damping: 26 }}
      >
        {children}
      </motion.span>
    </span>
  )
}
